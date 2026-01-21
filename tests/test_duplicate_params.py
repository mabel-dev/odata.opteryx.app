#!/usr/bin/env python3
"""Tests for duplicate OData query parameters being rejected."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.routes.v4.interface import router, require_authentication


def _make_client():
    app = FastAPI()
    app.dependency_overrides[require_authentication] = lambda: None
    app.include_router(router)
    return TestClient(app)


def test_duplicate_apply_rejected():
    client = _make_client()
    res = client.get("/api/v4/public/github/events?$apply=aggregate($count as c)&$apply=aggregate($count as d)")
    assert res.status_code == 400
    assert "Multiple parameters" in res.json().get("detail", "")


def test_duplicate_count_rejected():
    client = _make_client()
    res = client.get("/api/v4/public/github/events?$count=true&$count=false")
    assert res.status_code == 400
    assert "Multiple parameters" in res.json().get("detail", "")
