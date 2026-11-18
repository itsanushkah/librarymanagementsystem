import re

import pytest

from lms import config
from lms.db import Database
from lms.demo import seed_demo_data
from lms.services import Library
from lms.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "web.db")
    make_db = lambda: Database(backend="sqlite", sqlite_path=path)  # noqa: E731
    db = make_db()
    seed_demo_data(Library(db))
    db.close()
    monkeypatch.setattr(config, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "secret")
    app = create_app(db_factory=make_db)
    app.config["TESTING"] = True
    return app.test_client()


def csrf(client, url="/books"):
    html = client.get(url).get_data(as_text=True)
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    if m:
        return m.group(1)
    with client.session_transaction() as s:
        return s["csrf"]


def login(client):
    token = csrf(client, "/login")
    return client.post("/login", data={"csrf": token, "username": "admin",
                                       "password": "secret"}, follow_redirects=True)


def test_public_catalogue_and_detail(client):
    html = client.get("/books?q=hobbit").get_data(as_text=True)
    assert "The Hobbit" in html and "1984" not in html
    assert client.get("/books/1").status_code == 200
    # librarian pages need sign in
    assert client.get("/members").status_code == 302
    assert client.get("/").headers["Location"].endswith("/books")


def test_wrong_password(client):
    token = csrf(client, "/login")
    html = client.post("/login", data={"csrf": token, "username": "admin",
                                       "password": "nope"}).get_data(as_text=True)
    assert "Wrong username or password" in html


def test_post_without_csrf_rejected(client):
    login(client)
    assert client.post("/members/new", data={"name": "X", "email": "x@x.com"}).status_code == 400


def test_dashboard_and_pages_render(client):
    assert "Dashboard" in login(client).get_data(as_text=True)
    for url in ["/", "/members", "/members/1", "/loans", "/loans?status=overdue",
                "/loans?status=returned", "/loans?status=all", "/loans/issue",
                "/books/new", "/books/1/edit", "/books/2", "/healthz", "/reservations",
                "/fines", "/members/1/edit", "/books/6", "/books?page=2"]:
        assert client.get(url).status_code == 200, url


def test_full_flow(client):
    login(client)
    token = csrf(client, "/books/new")
    r = client.post("/books/new", data={"csrf": token, "isbn": "123", "title": "New Book",
                                        "author": "Someone", "copies": "1"})
    book_url = r.headers["Location"]
    book_id = int(book_url.rsplit("/", 1)[1])
    r = client.post("/members/new", data={"csrf": token, "name": "Zed",
                                          "email": "zed@example.com"})
    member_id = int(r.headers["Location"].rsplit("/", 1)[1])
    r = client.post("/loans/issue", data={"csrf": token, "book_id": book_id,
                                          "member_id": member_id}, follow_redirects=True)
    assert "Issued &#39;New Book&#39; to Zed" in r.get_data(as_text=True)
    # issuing again fails with a friendly message
    r = client.post("/loans/issue", data={"csrf": token, "book_id": book_id,
                                          "member_id": member_id},
                    headers={"Referer": "/loans/issue"}, follow_redirects=True)
    assert "already has a copy" in r.get_data(as_text=True)
    html = client.get(f"/members/{member_id}").get_data(as_text=True)
    loan_id = re.search(r"/loans/(\d+)/return", html).group(1)
    r = client.post(f"/loans/{loan_id}/return", data={"csrf": token},
                    follow_redirects=True)
    assert "Book returned" in r.get_data(as_text=True)


def test_open_redirect_blocked(client):
    login(client)
    token = csrf(client)
    r = client.post("/loans/2/renew", data={"csrf": token, "back": "//evil.com"})
    assert r.headers["Location"] == "/loans"


def test_404_page(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404 and "couldn" in r.get_data(as_text=True)


def test_security_headers(client):
    r = client.get("/books")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_login_lockout(client, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 3)
    token = csrf(client, "/login")
    for _ in range(3):
        client.post("/login", data={"csrf": token, "username": "admin", "password": "x"})
    r = client.post("/login", data={"csrf": token, "username": "admin", "password": "secret"})
    assert r.status_code == 429


def test_weak_password_blocked_in_production(client, monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "admin")
    token = csrf(client, "/login")
    r = client.post("/login", data={"csrf": token, "username": "admin", "password": "admin"})
    assert r.status_code == 403


def test_reserve_pay_edit_and_export(client):
    login(client)
    token = csrf(client)
    # Clean Code (book 6) has its only copy out and a waiting list in the demo data
    html = client.get("/books/6").get_data(as_text=True)
    assert "in line" in html and "Reserve" in html
    r = client.post("/reservations", data={"csrf": token, "book_id": 6, "member_id": 1,
                                           "back": "/books/6"}, follow_redirects=True)
    assert "Reserved &#39;Clean Code&#39; for Aarav Sharma" in r.get_data(as_text=True)
    assert "#3 in line" in client.get("/reservations").get_data(as_text=True)

    fines = client.get("/fines").get_data(as_text=True)
    loan_id = re.search(r"/loans/(\d+)/pay", fines).group(1)
    r = client.post(f"/loans/{loan_id}/pay", data={"csrf": token}, follow_redirects=True)
    assert "marked as paid" in r.get_data(as_text=True)

    r = client.post("/members/2/edit", data={"csrf": token, "name": "Priya V",
                                            "email": "priya.v@example.com"},
                    follow_redirects=True)
    assert "Priya V" in r.get_data(as_text=True)

    for kind in ("books", "members", "loans"):
        r = client.get(f"/export/{kind}.csv")
        assert r.status_code == 200 and r.mimetype == "text/csv"
    assert "Clean Code" in client.get("/export/books.csv").get_data(as_text=True)
    assert client.get("/export/nope.csv").status_code == 404


def test_return_announces_hold(client):
    login(client)
    token = csrf(client)
    html = client.get("/books/6").get_data(as_text=True)
    loan_id = re.search(r"/loans/(\d+)/return", html).group(1)
    r = client.post(f"/loans/{loan_id}/return", data={"csrf": token, "back": "/books/6"},
                    follow_redirects=True)
    assert "reserved for Meera Iyer" in r.get_data(as_text=True)
