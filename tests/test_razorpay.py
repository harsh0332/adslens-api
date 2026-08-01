import hashlib
import hmac
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_create_order_invalid_amount():
    response = client.post("/api/create-order", json={"amount": 100})
    assert response.status_code == 400
    assert "Invalid amount" in response.text

def test_create_order_missing_creds(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "")
    response = client.post("/api/create-order", json={"amount": 4900})
    assert response.status_code == 500


def test_create_order_auth_failure(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_mock_123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "mock_secret_456")

    mock_client = MagicMock()
    import razorpay.errors

    mock_client.order.create.side_effect = razorpay.errors.BadRequestError(
        "Authentication failed"
    )

    with patch("razorpay.Client", return_value=mock_client):
        response = client.post("/api/create-order", json={"amount": 4900})
        assert response.status_code == 500
        assert "Payment gateway rejected backend credentials" in response.json()["detail"]


def test_create_order_valid_amount(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_mock_123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "mock_secret_456")

    mock_razorpay_client = MagicMock()
    mock_razorpay_client.order.create.return_value = {
        "id": "order_mock_12345",
        "amount": 4900,
        "currency": "INR",
        "status": "created",
    }

    with patch("razorpay.Client", return_value=mock_razorpay_client):
        response = client.post("/api/create-order", json={"amount": 4900})
        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == "order_mock_12345"
        assert data["amount"] == 4900
        assert data["currency"] == "INR"
        assert data["key_id"] == "rzp_test_mock_123"

def test_verify_payment_missing_fields():
    response = client.post("/api/verify-payment", json={"razorpay_order_id": "order_123"})
    assert response.status_code == 400

def test_verify_payment_invalid_signature(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test_secret_789")
    response = client.post(
        "/api/verify-payment",
        json={
            "razorpay_order_id": "order_123",
            "razorpay_payment_id": "pay_456",
            "razorpay_signature": "invalid_bad_signature",
        },
    )
    assert response.status_code == 400
    assert response.json() == {"verified": False}

def test_verify_payment_valid_signature(monkeypatch):
    secret = "test_secret_789"
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", secret)
    
    order_id = "order_123"
    payment_id = "pay_456"
    msg = f"{order_id}|{payment_id}".encode("utf-8")
    valid_sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    response = client.post(
        "/api/verify-payment",
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": valid_sig,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"verified": True}
