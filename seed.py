"""Load sample books, members and loans: python seed.py"""
from lms.db import Database
from lms.demo import seed_demo_data
from lms.services import Library

if __name__ == "__main__":
    db = Database()
    lib = Library(db)
    if seed_demo_data(lib):
        print("Sample data loaded:", lib.stats())
    else:
        print("The library already has books; nothing loaded.")
    db.close()
