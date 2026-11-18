"""Flask web interface for the library."""
import csv
import hmac
import io
import json
import logging
import math
import secrets
import time
import urllib.request
from datetime import date
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config
from .db import Database
from .demo import seed_demo_data
from .services import Library, LibraryError

log = logging.getLogger("shelfwise")
WEAK_PASSWORDS = {"", "admin", "password", "change-me", "changeme", "123456"}


def create_app(db_factory=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__)
    secret = config.SECRET_KEY
    if not secret:
        if config.IS_PRODUCTION:
            log.warning("SECRET_KEY is not set; using a random one. "
                        "Sign-ins will reset when the app restarts.")
        secret = secrets.token_hex(32) if config.IS_PRODUCTION else "dev-secret"
    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.IS_PRODUCTION,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )
    # Render (and most hosts) put the app behind a proxy that terminates HTTPS.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db_factory = db_factory or Database
    login_failures = {}  # ip -> [count, locked_until]

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

    def same_site_referrer():
        ref = request.referrer or ""
        parsed = urlparse(ref)
        if parsed.netloc and parsed.netloc != request.host:
            return None
        return ref or None

    @app.errorhandler(LibraryError)
    def library_error(exc):
        flash(str(exc), "error")
        return redirect(same_site_referrer() or url_for("dashboard"))

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return render_template("error.html", code=exc.code, name=exc.name,
                               description=exc.description), exc.code

    @app.errorhandler(Exception)
    def server_error(exc):
        log.exception("Unhandled error on %s %s", request.method, request.path)
        return render_template("error.html", code=500, name="Something went wrong",
                               description="The error has been logged. "
                               "Please try again in a moment."), 500

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if config.IS_PRODUCTION:
            resp.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000; includeSubDomains")
        return resp

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
            holds=[r for r in l.list_reservations() if r["status"] == "ready"],
            recent=sorted(
                l.list_loans("all"), key=lambda r: (r["issued_on"], r["id"]),
                reverse=True)[:6])

    @app.route("/login", methods=["GET", "POST"])
    def login():
        weak = config.IS_PRODUCTION and config.ADMIN_PASSWORD in WEAK_PASSWORDS
        if request.method == "POST" and weak:
            flash("Sign-in is disabled until LMS_ADMIN_PASSWORD is set to a strong "
                  "password in the server settings.", "error")
            return render_template("login.html", weak=weak), 403
        ip = request.remote_addr or "?"
        fails = login_failures.get(ip, [0, 0])
        if request.method == "POST" and fails[1] > time.time():
            minutes = math.ceil((fails[1] - time.time()) / 60)
            flash(f"Too many wrong attempts. Try again in {minutes} minute"
                  f"{'s' if minutes != 1 else ''}.", "error")
            return render_template("login.html"), 429
        if request.method == "POST":
            user_ok = hmac.compare_digest(request.form.get("username", ""),
                                          config.ADMIN_USERNAME)
            pass_ok = hmac.compare_digest(request.form.get("password", ""),
                                          config.ADMIN_PASSWORD)
            if user_ok and pass_ok:
                login_failures.pop(ip, None)
                session.clear()
                session.permanent = True
                session["librarian"] = True
                flash("Welcome back!", "success")
                target = request.args.get("next", "")
                return redirect(target if target.startswith("/") and not
                                target.startswith("//") else url_for("dashboard"))
            fails[0] += 1
            if fails[0] >= config.LOGIN_MAX_ATTEMPTS:
                fails[:] = [0, time.time() + 60 * config.LOGIN_LOCK_MINUTES]
                log.warning("Sign-in locked for %s after repeated failures", ip)
            login_failures[ip] = fails
            flash("Wrong username or password.", "error")
        return render_template("login.html", weak=weak)

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
        held = lib().held_copies()
        for b in results:
            b["held"] = held.get(b["id"], 0)
        if available:
            results = [b for b in results if b["available_copies"] > b["held"]]
        pages = max(1, math.ceil(len(results) / config.PAGE_SIZE))
        page = min(max(request.args.get("page", 1, type=int), 1), pages)
        start = (page - 1) * config.PAGE_SIZE
        return render_template("books.html", books=results[start:start + config.PAGE_SIZE],
                               total=len(results), page=page, pages=pages, q=q,
                               genre=genre, available=available, genres=lib().genres())

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
        book["held"] = l.held_copies().get(book_id, 0)
        librarian = session.get("librarian")
        history = l.book_history(book_id) if librarian else []
        holds = l.book_reservations(book_id) if librarian else []
        queue = len(l._active_holds(book_id))
        return render_template("book_detail.html", book=book, members=members,
                               history=history, holds=holds, queue=queue)

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
            reservations=l.member_reservations(member_id),
            unpaid=l.unpaid_fines(member_id),
            total_fines=sum(x["fine"] for x in loans))

    @app.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
    @login_required
    def member_edit(member_id):
        member = lib().get_member(member_id)
        if request.method == "POST":
            f = request.form
            try:
                lib().update_member(member_id, f.get("name", "").strip(),
                                    f.get("email", "").strip(),
                                    f.get("phone", "").strip() or None)
            except LibraryError as exc:
                flash(str(exc), "error")
                return render_template("member_form.html", member={**member, **f})
            flash("Member details saved.", "success")
            return redirect(url_for("member_detail", member_id=member_id))
        return render_template("member_form.html", member=member)

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
        l = lib()
        fine = l.return_book(loan_id)
        flash("Book returned." + (f" Late fine: {fine}" if fine else " No fine due."),
              "success")
        for hold in l.promoted_holds:
            flash(f"Put this copy aside: it's reserved for {hold['member_name']} "
                  f"({hold['email']}).", "info")
        return redirect(back(url_for("loans")))

    @app.route("/loans/<int:loan_id>/renew", methods=["POST"])
    @login_required
    def loan_renew(loan_id):
        new_due = lib().renew_loan(loan_id)
        flash(f"Loan renewed until {new_due:%d %b %Y}.", "success")
        return redirect(back(url_for("loans")))

    @app.route("/loans/<int:loan_id>/pay", methods=["POST"])
    @login_required
    def loan_pay(loan_id):
        amount = lib().pay_fine(loan_id)
        flash(f"Fine of {amount} marked as paid.", "success")
        return redirect(back(url_for("fines")))

    @app.route("/fines")
    @login_required
    def fines():
        return render_template("fines.html", loans=lib().unpaid_fine_loans(),
                               stats=lib().stats())

    # reservations
    @app.route("/reservations")
    @login_required
    def reservations():
        return render_template("reservations.html",
                               reservations=lib().list_reservations())

    @app.route("/reservations", methods=["POST"])
    @login_required
    def reservation_new():
        l = lib()
        res_id = l.reserve_book(form_int("book_id"), form_int("member_id"))
        res = l.get_reservation(res_id)
        flash(f"Reserved '{res['title']}' for {res['member_name']}. We'll hold "
              "the next copy that comes back.", "success")
        return redirect(back(url_for("reservations")))

    @app.route("/reservations/<int:reservation_id>/cancel", methods=["POST"])
    @login_required
    def reservation_cancel(reservation_id):
        l = lib()
        res = l.cancel_reservation(reservation_id)
        flash(f"Cancelled {res['member_name']}'s reservation.", "success")
        for hold in l.promoted_holds:
            flash(f"The copy is now held for {hold['member_name']}.", "info")
        return redirect(back(url_for("reservations")))

    # exports
    def csv_response(name, rows, columns):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([label for label, _ in columns])
        for r in rows:
            writer.writerow(["" if r.get(k) is None else r.get(k) for _, k in columns])
        return Response(buf.getvalue(), mimetype="text/csv", headers={
            "Content-Disposition":
                f"attachment; filename={name}-{date.today().isoformat()}.csv"})

    @app.route("/export/<kind>.csv")
    @login_required
    def export(kind):
        l = lib()
        if kind == "books":
            return csv_response("books", l.search_books(), [
                ("ID", "id"), ("ISBN", "isbn"), ("Title", "title"), ("Author", "author"),
                ("Genre", "genre"), ("Year", "published_year"),
                ("Total copies", "total_copies"), ("Available", "available_copies")])
        if kind == "members":
            return csv_response("members", l.search_members(), [
                ("ID", "id"), ("Name", "name"), ("Email", "email"), ("Phone", "phone"),
                ("Joined", "joined_on"), ("Active", "active")])
        if kind == "loans":
            return csv_response("loans", l.list_loans("all"), [
                ("ID", "id"), ("Book", "title"), ("ISBN", "isbn"),
                ("Member", "member_name"), ("Issued", "issued_on"), ("Due", "due_on"),
                ("Returned", "returned_on"), ("Fine", "fine"), ("Fine paid", "fine_paid"),
                ("Status", "status")])
        abort(404)

    @app.route("/healthz")
    def healthz():
        lib().stats()
        return "ok"

    return app
