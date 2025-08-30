#!/usr/bin/env python3
"""
Movie Ticket Booking System — CLI (Python 3 + SQLite, no extra installs)

How to run (Windows/macOS/Linux):
1) Save this file as movie_booking.py
2) Open a terminal in the folder that contains the file.
3) Run:  python movie_booking.py   (or  py movie_booking.py  on Windows)

What this does:
- Creates a local SQLite database file `movie_booking.db` on first run
- Seeds a few movies and shows, with 20 seats per show (A1–A10, B1–B10)
- Lets you: list movies, view shows, see seat map, book seats, view your bookings, cancel a booking

Notes:
- No internet or extra packages needed. Everything uses Python's standard library.
- If you delete movie_booking.db, it will be recreated and reseeded on next run.
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple
import os
import sys

DB_FILE = "movie_booking.db"

# ----------------------------- DB SETUP ------------------------------------ #

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    # Enforce foreign keys
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    rating TEXT,
    duration_mins INTEGER
);

CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_id INTEGER NOT NULL,
    show_time TEXT NOT NULL,
    screen TEXT NOT NULL,
    price INTEGER NOT NULL,
    FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS seats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL,
    seat_label TEXT NOT NULL,
    is_booked INTEGER NOT NULL DEFAULT 0,
    UNIQUE(show_id, seat_label),
    FOREIGN KEY(show_id) REFERENCES shows(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    show_id INTEGER NOT NULL,
    seat_label TEXT NOT NULL,
    booked_at TEXT NOT NULL,
    amount_paid INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(show_id) REFERENCES shows(id) ON DELETE CASCADE
);
"""

SEAT_ROWS = ["A", "B"]
SEATS_PER_ROW = 10  # A1-A10, B1-B10

SEED_MOVIES = [
    ("Starlight Odyssey", "U/A", 128),
    ("The Last Mission", "U/A", 142),
    ("Laugh Out Loud", "U", 110),
]

# (movie_index_in_SEED_MOVIES, show_time_iso, screen, price)
SEED_SHOWS = [
    (0, "2025-08-29T18:00:00", "Screen 1", 220),
    (0, "2025-08-29T21:15:00", "Screen 1", 250),
    (1, "2025-08-29T19:30:00", "Screen 2", 240),
    (2, "2025-08-29T17:00:00", "Screen 3", 200),
]


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        # seed if empty
        cur = conn.execute("SELECT COUNT(*) AS c FROM movies;")
        if cur.fetchone()["c"] == 0:
            for title, rating, dur in SEED_MOVIES:
                conn.execute(
                    "INSERT INTO movies(title, rating, duration_mins) VALUES (?, ?, ?);",
                    (title, rating, dur),
                )
            conn.commit()

            # Insert shows
            for idx, show_time, screen, price in SEED_SHOWS:
                # movies are 1-indexed by AUTOINCREMENT
                movie_id = idx + 1
                conn.execute(
                    "INSERT INTO shows(movie_id, show_time, screen, price) VALUES (?, ?, ?, ?);",
                    (movie_id, show_time, screen, price),
                )
            conn.commit()

            # Insert seats for each show
            cur = conn.execute("SELECT id FROM shows;")
            for row in cur.fetchall():
                show_id = row["id"]
                for r in SEAT_ROWS:
                    for n in range(1, SEATS_PER_ROW + 1):
                        label = f"{r}{n}"
                        conn.execute(
                            "INSERT INTO seats(show_id, seat_label, is_booked) VALUES (?, ?, 0);",
                            (show_id, label),
                        )
            conn.commit()

# ---------------------------- DATA ACCESS ---------------------------------- #

def get_movies(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(conn.execute("SELECT id, title, rating, duration_mins FROM movies ORDER BY title;"))


def get_shows_by_movie(conn: sqlite3.Connection, movie_id: int) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT s.id, s.show_time, s.screen, s.price
            FROM shows s
            WHERE s.movie_id = ?
            ORDER BY s.show_time
            """,
            (movie_id,),
        )
    )


def get_show(conn: sqlite3.Connection, show_id: int) -> sqlite3.Row | None:
    cur = conn.execute(
        "SELECT s.id, s.movie_id, s.show_time, s.screen, s.price, m.title FROM shows s JOIN movies m ON m.id = s.movie_id WHERE s.id = ?;",
        (show_id,),
    )
    return cur.fetchone()


def seat_map(conn: sqlite3.Connection, show_id: int) -> List[Tuple[str, bool]]:
    cur = conn.execute(
        "SELECT seat_label, is_booked FROM seats WHERE show_id = ? ORDER BY seat_label;",
        (show_id,),
    )
    return [(r["seat_label"], bool(r["is_booked"])) for r in cur.fetchall()]


def ensure_user(conn: sqlite3.Connection, name: str, phone: str) -> int:
    try:
        cur = conn.execute("INSERT INTO users(name, phone) VALUES (?, ?);", (name.strip(), phone.strip()))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        # Already exists
        cur = conn.execute("SELECT id FROM users WHERE phone = ?;", (phone.strip(),))
        row = cur.fetchone()
        if not row:
            raise
        return row["id"]


def book_seats(conn: sqlite3.Connection, user_id: int, show_id: int, seat_labels: List[str]) -> List[int]:
    # Normalize labels
    seat_labels = [s.strip().upper() for s in seat_labels if s.strip()]
    if not seat_labels:
        raise ValueError("No seat labels provided")

    # Transaction
    try:
        cur = conn.execute("SELECT price FROM shows WHERE id = ?;", (show_id,))
        show_row = cur.fetchone()
        if not show_row:
            raise ValueError("Show not found")
        price = int(show_row["price"])

        booking_ids: List[int] = []
        now = datetime.now().isoformat(timespec="seconds")

        for label in seat_labels:
            # Check availability
            cur = conn.execute(
                "SELECT id, is_booked FROM seats WHERE show_id = ? AND seat_label = ?;",
                (show_id, label),
            )
            seat_row = cur.fetchone()
            if not seat_row:
                raise ValueError(f"Seat {label} does not exist for this show")
            if seat_row["is_booked"]:
                raise ValueError(f"Seat {label} is already booked")

            # Mark as booked
            conn.execute(
                "UPDATE seats SET is_booked = 1 WHERE id = ?;",
                (seat_row["id"],),
            )

            # Create booking
            cur = conn.execute(
                "INSERT INTO bookings(user_id, show_id, seat_label, booked_at, amount_paid) VALUES (?, ?, ?, ?, ?);",
                (user_id, show_id, label, now, price),
            )
            booking_ids.append(cur.lastrowid)

        conn.commit()
        return booking_ids
    except Exception:
        conn.rollback()
        raise


def get_bookings_by_phone(conn: sqlite3.Connection, phone: str) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT b.id, m.title, s.show_time, s.screen, b.seat_label, b.amount_paid, b.booked_at
            FROM bookings b
            JOIN shows s ON s.id = b.show_id
            JOIN movies m ON m.id = s.movie_id
            JOIN users u ON u.id = b.user_id
            WHERE u.phone = ?
            ORDER BY b.booked_at DESC
            """,
            (phone.strip(),),
        )
    )


def cancel_booking(conn: sqlite3.Connection, booking_id: int) -> bool:
    # Find booking
    cur = conn.execute("SELECT id, show_id, seat_label FROM bookings WHERE id = ?;", (booking_id,))
    row = cur.fetchone()
    if not row:
        return False
    show_id = row["show_id"]
    seat_label = row["seat_label"]

    try:
        conn.execute("DELETE FROM bookings WHERE id = ?;", (booking_id,))
        conn.execute(
            "UPDATE seats SET is_booked = 0 WHERE show_id = ? AND seat_label = ?;",
            (show_id, seat_label),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise

# ------------------------------ UI HELPERS --------------------------------- #

def print_heading(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def pause() -> None:
    input("\nPress Enter to continue...")


def show_movies(conn: sqlite3.Connection) -> None:
    print_heading("Movies")
    movies = get_movies(conn)
    if not movies:
        print("No movies found.")
        return
    for m in movies:
        print(f"[{m['id']}] {m['title']}  ({m['rating']}, {m['duration_mins']} mins)")


def show_shows_for_movie(conn: sqlite3.Connection) -> None:
    show_movies(conn)
    try:
        movie_id = int(input("\nEnter Movie ID to view shows: "))
    except ValueError:
        print("Invalid number.")
        return
    shows = get_shows_by_movie(conn, movie_id)
    print_heading("Shows")
    if not shows:
        print("No shows for this movie.")
        return
    for s in shows:
        print(f"[Show {s['id']}] {s['show_time']} • {s['screen']} • ₹{s['price']}")


def show_seat_map(conn: sqlite3.Connection) -> None:
    try:
        show_id = int(input("Enter Show ID to view seats: "))
    except ValueError:
        print("Invalid number.")
        return
    sh = get_show(conn, show_id)
    if not sh:
        print("Show not found.")
        return
    print_heading(f"Seat Map — {sh['title']} @ {sh['show_time']} • {sh['screen']}")
    seats = seat_map(conn, show_id)
    # Pretty print 10 per row
    current_row = None
    line = []
    for label, booked in seats:
        row_letter = label[0]
        if current_row is None:
            current_row = row_letter
        if row_letter != current_row:
            print(" ".join(line))
            line = []
            current_row = row_letter
        cell = f"{label}{'(X)' if booked else '( )'}"
        line.append(cell)
    if line:
        print(" ".join(line))


def make_booking(conn: sqlite3.Connection) -> None:
    try:
        show_id = int(input("Enter Show ID to book: "))
    except ValueError:
        print("Invalid number.")
        return
    sh = get_show(conn, show_id)
    if not sh:
        print("Show not found.")
        return
    name = input("Your Name: ").strip()
    phone = input("Your Phone (unique): ").strip()
    user_id = ensure_user(conn, name, phone)

    print("Enter seats separated by commas (e.g., A1,A2,B5). Type 'map' to view seats.")
    while True:
        seat_input = input("Seats: ").strip()
        if seat_input.lower() == "map":
            show_seat_map(conn)
            continue
        seat_labels = [s.strip().upper() for s in seat_input.split(",") if s.strip()]
        if not seat_labels:
            print("Please enter at least one seat.")
            continue
        try:
            ids = book_seats(conn, user_id, show_id, seat_labels)
            total = len(ids) * int(sh["price"])
            print("\nBooking confirmed! 🎟️")
            print(f"Movie: {sh['title']}")
            print(f"Show:  {sh['show_time']} • {sh['screen']}")
            print(f"Seats: {', '.join(seat_labels)}")
            print(f"Amount: ₹{total}")
            print(f"Booking IDs: {', '.join(map(str, ids))}")
            break
        except Exception as e:
            print(f"Error: {e}")
            print("Try again.")


def view_my_bookings(conn: sqlite3.Connection) -> None:
    phone = input("Enter your phone: ").strip()
    rows = get_bookings_by_phone(conn, phone)
    print_heading("Your Bookings")
    if not rows:
        print("No bookings found.")
        return
    for r in rows:
        print(
            f"[#{r['id']}] {r['title']} • {r['show_time']} • {r['screen']} • Seat {r['seat_label']} • ₹{r['amount_paid']} • at {r['booked_at']}"
        )


def cancel_my_booking(conn: sqlite3.Connection) -> None:
    try:
        booking_id = int(input("Enter Booking ID to cancel: "))
    except ValueError:
        print("Invalid number.")
        return
    ok = cancel_booking(conn, booking_id)
    if ok:
        print("Booking canceled and seat released.")
    else:
        print("Booking not found.")


MENU = """
================= Movie Ticket Booking — Main Menu =================
1) List Movies
2) List Shows for a Movie
3) View Seat Map for a Show
4) Book Seats
5) View My Bookings
6) Cancel a Booking
0) Exit
-------------------------------------------------------------------
Choose: """


def main() -> None:
    init_db()
    while True:
        try:
            choice = input(MENU).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if choice == "1":
            with get_conn() as conn:
                show_movies(conn)
                pause()
        elif choice == "2":
            with get_conn() as conn:
                show_shows_for_movie(conn)
                pause()
        elif choice == "3":
            with get_conn() as conn:
                show_seat_map(conn)
                pause()
        elif choice == "4":
            with get_conn() as conn:
                make_booking(conn)
                pause()
        elif choice == "5":
            with get_conn() as conn:
                view_my_bookings(conn)
                pause()
        elif choice == "6":
            with get_conn() as conn:
                cancel_my_booking(conn)
                pause()
        elif choice == "0":
            print("Bye! 👋, WELCOME AGAIN!!")
            break
        else:
            print("Invalid option. Try again.\n")


if __name__ == "__main__":
    main()
