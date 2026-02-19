"""
Pytest configuration and shared fixtures for Stash Pro tests
"""

import pytest
import requests
import os
import uuid

# Base URL from environment
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://pro-features-preview.preview.emergentagent.com').rstrip('/')


@pytest.fixture
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture
def test_user():
    """Create a fresh test user and return token"""
    email = f"test_{uuid.uuid4().hex[:8]}@test.com"
    response = requests.post(f"{BASE_URL}/api/auth/register", json={
        "email": email,
        "password": "testpass123",
        "name": "Test User"
    })
    if response.status_code == 200:
        return {
            "email": email,
            "token": response.json()["access_token"],
            "user": response.json()["user"]
        }
    pytest.skip(f"Failed to create test user: {response.text}")


@pytest.fixture
def free_user_headers(test_user):
    """Headers for authenticated free user"""
    return {"Authorization": f"Bearer {test_user['token']}"}


@pytest.fixture
def pro_user_headers():
    """Create a Pro user and return auth headers"""
    email = f"test_pro_{uuid.uuid4().hex[:8]}@test.com"
    response = requests.post(f"{BASE_URL}/api/auth/register", json={
        "email": email,
        "password": "testpass123",
        "name": "Pro Test User"
    })
    if response.status_code != 200:
        pytest.skip(f"Failed to create test user: {response.text}")
    
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Upgrade to Pro
    upgrade_response = requests.post(f"{BASE_URL}/api/users/upgrade-pro", headers=headers)
    if upgrade_response.status_code != 200:
        pytest.skip(f"Failed to upgrade to Pro: {upgrade_response.text}")
    
    return headers
