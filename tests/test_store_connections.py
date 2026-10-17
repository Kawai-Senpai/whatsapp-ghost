"""Connections owned by threads that have exited must be closed.

Starlette runs sync endpoints on anyio worker threads, which are retired after
a few idle seconds and replaced by new ones. Store keeps one connection per
thread, and it used to keep every connection it ever opened in a list, so each
retired thread leaked two file descriptors (db + wal). On dev that reached the
1024 open-files limit and every request, including /phone and /console, failed
with "OSError: [Errno 24] Too many open files".
"""

from __future__ import annotations

import threading

from whatsapp_ghost.db import Store


def _query_in_new_thread(store: Store) -> None:
    def work() -> None:
        with store.connect() as db:
            db.execute("SELECT 1").fetchone()

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()


def test_connections_of_exited_threads_are_closed(tmp_path):
    store = Store(tmp_path / "ghost.db")
    for _ in range(50):
        _query_in_new_thread(store)
    # Each new thread reaps the ones that already exited, so only the most
    # recent (now dead) thread's connection can still be held.
    assert store.open_connection_count() <= 1

    with store.connect() as db:
        db.execute("SELECT 1").fetchone()
    # Opening on this live thread reaped the last dead one.
    assert store.open_connection_count() == 1
    store.close_all()
    assert store.open_connection_count() == 0


def test_live_thread_keeps_its_connection(tmp_path):
    store = Store(tmp_path / "ghost.db")
    with store.connect() as first:
        pass
    _query_in_new_thread(store)
    with store.connect() as second:
        second.execute("SELECT 1").fetchone()
    assert first is second
    store.close_all()
