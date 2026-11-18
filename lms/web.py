"""Flask web interface for the library."""
import hmac
import json
import secrets
import urllib.request
from datetime import date
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

from . import config
from .db import Database
from .demo import seed_demo_data
from .services import Library, LibraryError


def create_app(db_factory=None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY
    db_factory = db_factory or Database

    # -- database per request -------------------------------------------
    def lib():
        if "lib" not in g:
            g.db = db_factory()
            g.lib = Library(g.db)
        return g.lib

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    if config.SEED_DEMO_DATA:
        with app.app_context():
            try:
                seed_demo_data(lib())
            except LibraryError:
                pass  # another worker seeded at the same time

    # -- auth & csrf -----------------------------------------------------
    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("librarian"):
                flash("Please sign in as a librarian to do that.", "info")
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf", "")
            if not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Invalid form token. Please reload the page and try again.")

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "is_librarian": session.get("librarian"),
                "today": date.today()}

    @app.errorhandler(LibraryError)
    def library_error(exc):
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("dashboard"))

    def back(default):
        target = request.form.get("back", "")
        return target if target.startswith("/") and not target.startswith("//") \
            else default

    def form_int(name, required=True):
        raw = request.form.get(name, "").strip()
        if not raw:
            if required:
                raise LibraryError(f"{name.replace('_', ' ').capitalize()} is required.")
            return None
        try:
            return int(raw)
        except ValueError:
            raise LibraryError(f"{name.replace('_', ' ').capitalize()} must be a number.")

    # -- pages -----------------------------------------------------------
    @app.route("/")
    def dashboard():
        if not session.get("librarian"):
            return redirect(url_for("books"))
        l = lib()
        return render_template(
            "dashboard.html", stats=l.stats(), overdue=l.overdue_loans(),
            attention=[x for x in l.list_loans("active")
                       if x["status"] in ("overdue", "due soon")],
            popular=l.popular_books(5),
            recent=sorted(
                l.list_loans("all"), key=lambda r: (r["issued_on"], r["id"]),
                reverse=True)[:6])

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user_ok = hmac.compare_digest(request.form.get("username", ""),
                                          config.ADMIN_USERNAME)
            pass_ok = hmac.compare_digest(request.form.get("password", ""),
                                          config.ADMIN_PASSWORD)
            if user_ok and pass_ok:
                session.clear()
                session["librarian"] = True
                flash("Welcome back!", "success")
                target = request.args.get("next", "")
                return redirect(target if target.startswith("/") and not
                                target.startswith("//") else url_for("dashboard"))
            flash("Wrong username or password.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("books"))

    # books
    @app.route("/books")
    def books():
        q = request.args.get("q", "").strip()
        genre = request.args.get("genre", "")
        available = request.args.get("available") == "1"
        results = lib().search_books(q, available_only=available)
        if genre:
            results = [b for b in results if b["genre"] == genre]
        return render_template("books.html", books=results, q=q, genre=genre,
                               available=available, genres=lib().genres())

    @app.route("/books/new", methods=["GET", "POST"])
    @login_required
    def book_new():
        if request.method == "POST":
            f = request.form
            try:
                book_id = lib().add_book(
                    f.get("isbn", "").strip(), f.get("title", "").strip(),
                    f.get("author", "").strip(), f.get("genre", "").strip() or None,
                    form_int("published_year", required=False),
                    form_int("copies") if f.get("copies") else 1)
            except LibraryError as exc:
                flash(str(exc), "error")
                return render_template("book_form.html", book=f, new=True)
            flash("Book added to the catalogue.", "success")
            return redirect(url_for("book_detail", book_id=book_id))
        return render_template("book_form.html", book={}, new=True)

    @app.route("/books/<int:book_id>")
    def book_detail(book_id):
        l = lib()
        book = l.get_book(book_id)
        members = [m for m in l.search_members() if m["active"]] \
            if session.get("librarian") else []
        history = l.book_history(book_id) if session.get("librarian") else []
        return render_template("book_detail.html", book=book, members=members,
                               history=history)

    @app.route("/books/<int:book_id>/edit", methods=["GET", "POST"])
    @login_required
    def book_edit(book_id):
        book = lib().get_book(book_id)
        if request.method == "POST":
            f = request.form
            try:
                lib().update_book(book_id, isbn=f.get("isbn", "").strip(),
                                  title=f.get("title", "").strip(),
                                  author=f.get("author", "").strip(),
                                  genre=f.get("genre", "").strip(),
                                  published_year=form_int("published_year", False))
            except LibraryError as exc:
                flash(str(exc), "error")
                return render_template("book_form.html", book={**book, **f}, new=False)
            flash("Book updated.", "success")
            return redirect(url_for("book_detail", book_id=book_id))
        return render_template("book_form.html", book=book, new=False)

    @app.route("/books/<int:book_id>/copies", methods=["POST"])
    @login_required
    def book_copies(book_id):
        delta = form_int("delta")
        lib().change_copies(book_id, delta)
        flash(f"{'Added' if delta > 0 else 'Removed'} {abs(delta)} "
              f"cop{'y' if abs(delta) == 1 else 'ies'}.", "success")
        return redirect(url_for("book_detail", book_id=book_id))

    @app.route("/books/<int:book_id>/delete", methods=["POST"])
    @login_required
    def book_delete(book_id):
        title = lib().get_book(book_id)["title"]
        lib().delete_book(book_id)
        flash(f"Deleted '{title}'.", "success")
        return redirect(url_for("books"))

    @app.route("/api/isbn/<isbn>")
    @login_required
    def isbn_lookup(isbn):
        """Fill in book details from Open Library."""
        isbn = "".join(c for c in isbn if c.isalnum())
        url = ("https://openlibrary.org/api/books?format=json&jscmd=data"
               f"&bibkeys=ISBN:{isbn}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "library-app"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.load(resp).get(f"ISBN:{isbn}")
        except Exception:
            return jsonify({"error": "Could not reach Open Library."}), 502
        if not data:
            return jsonify({"error": "No book found for that ISBN."}), 404
        year = "".join(c for c in data.get("publish_date", "") if c.isdigit())[-4:]
        subjects = [s["name"] for s in data.get("subjects", [])][:1]
        return jsonify({
            "title": data.get("title", ""),
            "author": ", ".join(a["name"] for a in data.get("authors", [])),
            "published_year": year,
            "genre": subjects[0] if subjects else "",
        })

    # members
    @app.route("/members")
    @login_required
    def members():
        q = request.args.get("q", "").strip()
        rows = lib().search_members(q)
        counts = {r["member_id"]: r["n"] for r in lib().db.query(
            "SELECT member_id, COUNT(*) AS n FROM loans WHERE returned_on IS NULL "
            "GROUP BY member_id")}
        for m in rows:
            m["on_loan"] = counts.get(m["id"], 0)
        return render_template("members.html", members=rows, q=q)

    @app.route("/members/new", methods=["GET", "POST"])
    @login_required
    def member_new():
        if request.method == "POST":
            f = request.form
            try:
                member_id = lib().add_member(f.get("name", "").strip(),
                                             f.get("email", "").strip(),
                                             f.get("phone", "").strip() or None)
            except LibraryError as exc:
                flash(str(exc), "error")
                return render_template("member_form.html", member=f)
            flash("Member registered.", "success")
            return redirect(url_for("member_detail", member_id=member_id))
        return render_template("member_form.html", member={})

    @app.route("/members/<int:member_id>")
    @login_required
    def member_detail(member_id):
        l = lib()
        member = l.get_member(member_id)
        loans = l.member_loans(member_id)
        books = l.search_books(available_only=True)
        return render_template(
            "member_detail.html", member=member, books=books,
            active=[x for x in loans if x["status"] != "returned"],
            past=[x for x in loans if x["status"] == "returned"],
            total_fines=sum(x["fine"] for x in loans))

    @app.route("/members/<int:member_id>/toggle", methods=["POST"])
    @login_required
    def member_toggle(member_id):
        member = lib().get_member(member_id)
        lib().set_member_active(member_id, not member["active"])
        flash(f"{member['name']} is now {'inactive' if member['active'] else 'active'}.",
              "success")
        return redirect(url_for("member_detail", member_id=member_id))

    # loans
    @app.route("/loans")
    @login_required
    def loans():
        status = request.args.get("status", "active")
        if status not in ("active", "overdue", "returned", "all"):
            status = "active"
        q = request.args.get("q", "").strip()
        return render_template("loans.html", loans=lib().list_loans(status, term=q),
                               current=status, q=q)

    @app.route("/loans/issue", methods=["GET", "POST"])
    @login_required
    def loan_issue():
        l = lib()
        if request.method == "POST":
            loan_id = l.issue_book(form_int("book_id"), form_int("member_id"))
            loan = l.get_loan(loan_id)
            flash(f"Issued '{loan['title']}' to {loan['member_name']}. "
                  f"Due {loan['due_on']:%d %b %Y}.", "success")
            return redirect(back(url_for("loans")))
        return render_template(
            "issue.html", books=l.search_books(available_only=True),
            members=[m for m in l.search_members() if m["active"]],
            book_id=request.args.get("book_id", type=int),
            member_id=request.args.get("member_id", type=int),
            loan_days=l.loan_days)

    @app.route("/loans/<int:loan_id>/return", methods=["POST"])
    @login_required
    def loan_return(loan_id):
        fine = lib().return_book(loan_id)
        flash("Book returned." + (f" Late fine: {fine}" if fine else " No fine due."),
              "success")
        return redirect(back(url_for("loans")))

    @app.route("/loans/<int:loan_id>/renew", methods=["POST"])
    @login_required
    def loan_renew(loan_id):
        new_due = lib().renew_loan(loan_id)
        flash(f"Loan renewed until {new_due:%d %b %Y}.", "success")
        return redirect(back(url_for("loans")))

    @app.route("/healthz")
    def healthz():
        lib().stats()
        return "ok"

    return app
