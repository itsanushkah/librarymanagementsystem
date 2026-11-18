import os
from datetime import date, timedelta
from decimal import Decimal

import pytest

from lms.db import Database
from lms.services import Library, LibraryError

TODAY = date(2026, 1, 10)


@pytest.fixture
def lib():
    backend = os.getenv("LMS_TEST_BACKEND", "sqlite")
    db = Database(backend=backend, sqlite_path=":memory:",
                  database_url=os.getenv("LMS_TEST_DATABASE_URL"))
    if backend in ("mysql", "postgres"):
        cur = db.conn.cursor()
        for t in ("reservations", "loans", "members", "books"):
            cur.execute(f"DELETE FROM {t}")
        if backend == "postgres":
            cur.execute("TRUNCATE reservations, loans, members, books RESTART IDENTITY")
        db.conn.commit()
    yield Library(db, loan_days=14, max_active_loans=2, fine_per_day=5,
                  max_unpaid_fines=20, max_reservations=2, hold_days=3)
    db.close()


@pytest.fixture
def book(lib):
    return lib.add_book("111", "Dune", "Frank Herbert", "Sci-Fi", 1965, copies=2)


@pytest.fixture
def member(lib):
    return lib.add_member("Asha", "asha@example.com")


def test_add_and_search_books(lib, book):
    lib.add_book("222", "Emma", "Jane Austen", "Classic")
    assert [b["title"] for b in lib.search_books("dune")] == ["Dune"]
    assert [b["title"] for b in lib.search_books("austen")] == ["Emma"]
    assert [b["title"] for b in lib.search_books("sci")] == ["Dune"]
    assert len(lib.search_books()) == 2


def test_duplicate_isbn_rejected(lib, book):
    with pytest.raises(LibraryError):
        lib.add_book("111", "Other", "Someone")


def test_duplicate_member_email_rejected(lib, member):
    with pytest.raises(LibraryError):
        lib.add_member("Other Asha", "ASHA@example.com")


def test_update_book(lib, book):
    lib.update_book(book, title="Dune Messiah", genre="")
    b = lib.get_book(book)
    assert b["title"] == "Dune Messiah" and b["genre"] == "Sci-Fi"


def test_issue_and_return_on_time(lib, book, member):
    loan = lib.issue_book(book, member, today=TODAY)
    assert lib.get_book(book)["available_copies"] == 1
    assert lib.get_loan(loan)["due_on"] == TODAY + timedelta(days=14)
    assert lib.return_book(loan, today=TODAY + timedelta(days=3)) == Decimal("0.00")
    assert lib.get_book(book)["available_copies"] == 2


def test_late_return_charges_fine(lib, book, member):
    loan = lib.issue_book(book, member, today=TODAY)
    fine = lib.return_book(loan, today=TODAY + timedelta(days=17))
    assert fine == Decimal("15.00")
    assert lib.member_history(member)[0]["fine"] == Decimal("15.00")
    assert lib.stats()["fines_outstanding"] == Decimal("15.00")
    assert lib.unpaid_fines(member) == Decimal("15.00")
    assert lib.pay_fine(loan) == Decimal("15.00")
    assert lib.stats()["fines_collected"] == Decimal("15.00")
    assert lib.unpaid_fines(member) == 0
    with pytest.raises(LibraryError, match="already been paid"):
        lib.pay_fine(loan)


def test_cannot_return_twice(lib, book, member):
    loan = lib.issue_book(book, member, today=TODAY)
    lib.return_book(loan, today=TODAY)
    with pytest.raises(LibraryError):
        lib.return_book(loan, today=TODAY)


def test_no_copies_available(lib, member):
    single = lib.add_book("333", "Solo", "A. Writer")
    other = lib.add_member("Ben", "ben@example.com")
    lib.issue_book(single, member, today=TODAY)
    with pytest.raises(LibraryError, match="No copies"):
        lib.issue_book(single, other, today=TODAY)


def test_loan_limit(lib, member):
    ids = [lib.add_book(str(i), f"Book {i}", "X") for i in range(3)]
    lib.issue_book(ids[0], member, today=TODAY)
    lib.issue_book(ids[1], member, today=TODAY)
    with pytest.raises(LibraryError, match="already has 2"):
        lib.issue_book(ids[2], member, today=TODAY)


def test_same_book_twice_rejected(lib, book, member):
    lib.issue_book(book, member, today=TODAY)
    with pytest.raises(LibraryError, match="already has a copy"):
        lib.issue_book(book, member, today=TODAY)


def test_overdue_blocks_new_loans_and_shows_in_report(lib, book, member):
    other = lib.add_book("444", "Other", "Y")
    lib.issue_book(book, member, today=TODAY)
    later = TODAY + timedelta(days=20)
    with pytest.raises(LibraryError, match="overdue"):
        lib.issue_book(other, member, today=later)
    report = lib.overdue_loans(today=later)
    assert len(report) == 1
    assert report[0]["days_overdue"] == 6
    assert report[0]["fine_so_far"] == Decimal("30.00")


def test_inactive_member_cannot_borrow(lib, book, member):
    lib.set_member_active(member, False)
    with pytest.raises(LibraryError, match="inactive"):
        lib.issue_book(book, member, today=TODAY)


def test_cannot_deactivate_member_with_loans(lib, book, member):
    lib.issue_book(book, member, today=TODAY)
    with pytest.raises(LibraryError):
        lib.set_member_active(member, False)


def test_renew(lib, book, member):
    loan = lib.issue_book(book, member, today=TODAY)
    assert lib.renew_loan(loan, today=TODAY) == TODAY + timedelta(days=28)
    with pytest.raises(LibraryError):
        lib.renew_loan(loan, today=TODAY + timedelta(days=40))


def test_copies_and_delete_rules(lib, book, member):
    lib.change_copies(book, 3)
    assert lib.get_book(book)["total_copies"] == 5
    loan = lib.issue_book(book, member, today=TODAY)
    with pytest.raises(LibraryError):
        lib.change_copies(book, -5)
    with pytest.raises(LibraryError):
        lib.delete_book(book)
    lib.return_book(loan, today=TODAY)
    with pytest.raises(LibraryError, match="history"):
        lib.delete_book(book)
    fresh = lib.add_book("555", "Fresh", "Z")
    lib.delete_book(fresh)
    with pytest.raises(LibraryError):
        lib.get_book(fresh)


def test_popular_books_and_stats(lib, book, member):
    other = lib.add_member("Ben", "ben@example.com")
    for m in (member, other):
        loan = lib.issue_book(book, m, today=TODAY)
        lib.return_book(loan, today=TODAY)
    assert lib.popular_books()[0]["times_borrowed"] == 2
    s = lib.stats()
    assert s["titles"] == 1 and s["members"] == 2 and s["on_loan"] == 0


def test_unpaid_fines_block_borrowing(lib, book, member):
    loan = lib.issue_book(book, member, today=TODAY)
    lib.return_book(loan, today=TODAY + timedelta(days=19))  # 25 owed, limit 20
    with pytest.raises(LibraryError, match="unpaid fines"):
        lib.issue_book(book, member, today=TODAY + timedelta(days=20))
    lib.pay_fine(loan)
    lib.issue_book(book, member, today=TODAY + timedelta(days=20))


def test_update_member(lib, member):
    other = lib.add_member("Ben", "ben@example.com")
    lib.update_member(member, "Asha K", "asha.k@example.com", "123")
    m = lib.get_member(member)
    assert (m["name"], m["email"], m["phone"]) == ("Asha K", "asha.k@example.com", "123")
    with pytest.raises(LibraryError, match="already uses"):
        lib.update_member(other, "Ben", "asha.k@example.com")


@pytest.fixture
def single(lib):
    return lib.add_book("900", "Only Copy", "A. Writer")


def test_reservation_queue_and_hold(lib, single, member):
    ben = lib.add_member("Ben", "ben@example.com")
    cy = lib.add_member("Cy", "cy@example.com")
    with pytest.raises(LibraryError, match="on the shelf"):
        lib.reserve_book(single, ben, today=TODAY)
    loan = lib.issue_book(single, member, today=TODAY)
    lib.reserve_book(single, ben, today=TODAY)
    lib.reserve_book(single, cy, today=TODAY + timedelta(days=1))
    with pytest.raises(LibraryError, match="already reserved"):
        lib.reserve_book(single, ben, today=TODAY)
    assert [r["position"] for r in lib.book_reservations(single)] == [1, 2]
    with pytest.raises(LibraryError, match="reserved"):
        lib.renew_loan(loan, today=TODAY)

    lib.return_book(loan, today=TODAY + timedelta(days=5))
    assert [h["member_id"] for h in lib.promoted_holds] == [ben]
    # the copy on the shelf is held for Ben, not Cy or anyone else
    with pytest.raises(LibraryError, match="held for Ben"):
        lib.issue_book(single, cy, today=TODAY + timedelta(days=5))
    lib.issue_book(single, ben, today=TODAY + timedelta(days=6))
    assert lib.member_reservations(ben) == []
    assert lib.member_reservations(cy)[0]["position"] == 1


def test_hold_expires_and_passes_on(lib, single, member):
    ben = lib.add_member("Ben", "ben@example.com")
    cy = lib.add_member("Cy", "cy@example.com")
    loan = lib.issue_book(single, member, today=TODAY)
    lib.reserve_book(single, ben, today=TODAY)
    lib.reserve_book(single, cy, today=TODAY)
    lib.return_book(loan, today=TODAY + timedelta(days=2))
    promoted = lib.process_holds(single, today=TODAY + timedelta(days=6))
    assert [h["member_id"] for h in promoted] == [cy]
    assert lib.member_reservations(ben) == []


def test_cancel_reservation_passes_hold_on(lib, single, member):
    ben = lib.add_member("Ben", "ben@example.com")
    cy = lib.add_member("Cy", "cy@example.com")
    loan = lib.issue_book(single, member, today=TODAY)
    first = lib.reserve_book(single, ben, today=TODAY)
    lib.reserve_book(single, cy, today=TODAY)
    lib.return_book(loan, today=TODAY + timedelta(days=1))
    lib.cancel_reservation(first, today=TODAY + timedelta(days=1))
    assert [h["member_id"] for h in lib.promoted_holds] == [cy]
    with pytest.raises(LibraryError, match="no longer active"):
        lib.cancel_reservation(first)


def test_deactivating_member_cancels_reservations(lib, single, member):
    ben = lib.add_member("Ben", "ben@example.com")
    lib.issue_book(single, member, today=TODAY)
    lib.reserve_book(single, ben, today=TODAY)
    lib.set_member_active(ben, False)
    assert lib.book_reservations(single) == []


def test_reservation_limit(lib, member):
    ben = lib.add_member("Ben", "ben@example.com")
    ids = [lib.add_book(f"r{i}", f"R{i}", "X") for i in range(3)]
    for b in ids:
        lib.issue_book(b, ben, today=TODAY) if b != ids[2] else None
    lib.issue_book(ids[2], lib.add_member("Cy", "cy@example.com"), today=TODAY)
    lib.reserve_book(ids[0], member, today=TODAY)
    lib.reserve_book(ids[1], member, today=TODAY)
    with pytest.raises(LibraryError, match="2 reservations"):
        lib.reserve_book(ids[2], member, today=TODAY)
