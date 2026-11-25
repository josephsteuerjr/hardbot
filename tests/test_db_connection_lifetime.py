"""Connections must release FDs without relying on garbage collection."""
import gc
import sqlite3

import pytest

import db


def test_context_closes_even_when_connections_are_retained(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'lifetime.db')
    retained = []
    collecting = gc.isenabled()
    gc.disable()
    try:
        for _ in range(400):
            with db.connect() as conn:
                assert conn.execute('SELECT 1').fetchone()[0] == 1
                retained.append(conn)
        for conn in retained:
            with pytest.raises(sqlite3.ProgrammingError, match='closed'):
                conn.execute('SELECT 1')
    finally:
        for conn in retained:
            conn.close()
        if collecting:
            gc.enable()


def test_context_commits_and_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'lifetime.db')
    with db.connect() as conn:
        conn.execute('CREATE TABLE sample(value TEXT)')
        conn.execute('INSERT INTO sample VALUES (?)', ('accepted',))
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')
    with db.connect() as check:
        assert check.execute('SELECT value FROM sample').fetchone()[0] == 'accepted'


def test_context_rolls_back_and_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'lifetime.db')
    with db.connect() as conn:
        conn.execute('CREATE TABLE sample(value TEXT)')
    with pytest.raises(ValueError, match='reject'):
        with db.connect() as conn:
            conn.execute('INSERT INTO sample VALUES (?)', ('rejected',))
            raise ValueError('reject')
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')
    with db.connect() as check:
        assert check.execute('SELECT COUNT(*) FROM sample').fetchone()[0] == 0
