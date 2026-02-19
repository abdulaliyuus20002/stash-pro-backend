"""
Test suite for Stash Pro subscription features
Tests:
- User registration (starts with free plan)
- Pro upgrade endpoint
- Get user plan endpoint
- Advanced search (Pro only)
- Vault export (Pro only)
- Smart reminders (Pro only)
- Collection limit enforcement (5 for free)
- Cancel Pro subscription
"""

import pytest
import requests
import os
import uuid
from datetime import datetime

# Base URL from environment
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://pro-features-preview.preview.emergentagent.com').rstrip('/')

# Test credentials
FREE_USER_EMAIL = f"test_free_{uuid.uuid4().hex[:8]}@test.com"
PRO_USER_EMAIL = f"test_pro_{uuid.uuid4().hex[:8]}@test.com"
TEST_PASSWORD = "testpass123"


class TestHealthCheck:
    """Basic health check tests"""
    
    def test_api_health(self):
        """Test API health endpoint"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        print(f"API Health: {data}")


class TestUserRegistration:
    """Test user registration - should start with free plan"""
    
    def test_register_new_user_starts_free(self):
        """Verify new users start with free plan"""
        email = f"test_reg_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": email,
            "password": TEST_PASSWORD,
            "name": "Test User"
        })
        
        assert response.status_code == 200, f"Registration failed: {response.text}"
        data = response.json()
        
        # Verify user response structure
        assert "access_token" in data, "No access token in response"
        assert "user" in data, "No user in response"
        
        # Verify user starts with free plan
        user = data["user"]
        assert user["plan_type"] == "free", f"Expected plan_type=free, got {user['plan_type']}"
        print(f"New user registered with plan_type: {user['plan_type']}")
        
        return data["access_token"]


class TestProUpgrade:
    """Test Pro upgrade endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Create a fresh test user for each test"""
        self.email = f"test_upgrade_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.email,
            "password": TEST_PASSWORD,
            "name": "Upgrade Test User"
        })
        assert response.status_code == 200, f"Registration failed: {response.text}"
        self.token = response.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
    
    def test_upgrade_to_pro(self):
        """Test upgrading user to Pro plan"""
        # First verify user is on free plan
        response = requests.get(f"{BASE_URL}/api/users/plan", headers=self.headers)
        assert response.status_code == 200
        data = response.json()
        assert data["plan_type"] == "free", "User should start on free plan"
        
        # Upgrade to Pro
        response = requests.post(f"{BASE_URL}/api/users/upgrade-pro", headers=self.headers)
        assert response.status_code == 200, f"Upgrade failed: {response.text}"
        data = response.json()
        
        assert data["plan_type"] == "pro", "Plan should be pro after upgrade"
        assert "pro_expires_at" in data, "Should have pro_expires_at"
        print(f"Upgrade response: {data}")
        
        # Verify plan is now Pro
        response = requests.get(f"{BASE_URL}/api/users/plan", headers=self.headers)
        assert response.status_code == 200
        data = response.json()
        assert data["is_pro"] == True, "is_pro should be True"
        assert data["plan_type"] == "pro", "plan_type should be pro"
        print(f"User plan after upgrade: {data['plan_type']}")


class TestGetUserPlan:
    """Test get user plan endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Create test users"""
        # Free user
        self.free_email = f"test_plan_free_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.free_email,
            "password": TEST_PASSWORD,
        })
        self.free_token = response.json()["access_token"]
        self.free_headers = {"Authorization": f"Bearer {self.free_token}"}
        
        # Pro user (register + upgrade)
        self.pro_email = f"test_plan_pro_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.pro_email,
            "password": TEST_PASSWORD,
        })
        self.pro_token = response.json()["access_token"]
        self.pro_headers = {"Authorization": f"Bearer {self.pro_token}"}
        requests.post(f"{BASE_URL}/api/users/upgrade-pro", headers=self.pro_headers)
    
    def test_free_user_plan_limits(self):
        """Test free user plan returns correct limits"""
        response = requests.get(f"{BASE_URL}/api/users/plan", headers=self.free_headers)
        assert response.status_code == 200
        data = response.json()
        
        # Verify free plan limits
        assert data["plan_type"] == "free"
        assert data["is_pro"] == False
        assert data["limits"]["max_collections"] == 5, "Free should have 5 collections limit"
        assert data["limits"]["max_items"] == 50, "Free should have 50 items limit"
        assert data["limits"]["advanced_search"] == False
        assert data["limits"]["smart_reminders"] == False
        assert data["limits"]["vault_export"] == False
        assert data["limits"]["ai_features"] == False
        
        # Verify features
        assert data["features"]["unlimited_collections"] == False
        assert data["features"]["advanced_search"] == False
        assert data["features"]["smart_reminders"] == False
        assert data["features"]["vault_export"] == False
        print(f"Free user limits: {data['limits']}")
    
    def test_pro_user_plan_limits(self):
        """Test pro user plan returns correct limits"""
        response = requests.get(f"{BASE_URL}/api/users/plan", headers=self.pro_headers)
        assert response.status_code == 200
        data = response.json()
        
        # Verify pro plan limits
        assert data["plan_type"] == "pro"
        assert data["is_pro"] == True
        assert data["limits"]["max_collections"] == -1, "Pro should have unlimited collections"
        assert data["limits"]["max_items"] == -1, "Pro should have unlimited items"
        assert data["limits"]["advanced_search"] == True
        assert data["limits"]["smart_reminders"] == True
        assert data["limits"]["vault_export"] == True
        assert data["limits"]["ai_features"] == True
        
        # Verify features
        assert data["features"]["unlimited_collections"] == True
        assert data["features"]["advanced_search"] == True
        assert data["features"]["smart_reminders"] == True
        assert data["features"]["vault_export"] == True
        print(f"Pro user limits: {data['limits']}")


class TestAdvancedSearch:
    """Test advanced search endpoint - Pro only"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Create test users"""
        # Free user
        self.free_email = f"test_search_free_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.free_email,
            "password": TEST_PASSWORD,
        })
        self.free_token = response.json()["access_token"]
        self.free_headers = {"Authorization": f"Bearer {self.free_token}"}
        
        # Pro user
        self.pro_email = f"test_search_pro_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.pro_email,
            "password": TEST_PASSWORD,
        })
        self.pro_token = response.json()["access_token"]
        self.pro_headers = {"Authorization": f"Bearer {self.pro_token}"}
        requests.post(f"{BASE_URL}/api/users/upgrade-pro", headers=self.pro_headers)
    
    def test_advanced_search_forbidden_for_free_user(self):
        """Free users should get 403 on advanced search"""
        response = requests.get(
            f"{BASE_URL}/api/search/advanced",
            params={"q": "test"},
            headers=self.free_headers
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        data = response.json()
        assert "Pro feature" in data.get("detail", ""), "Should mention Pro feature in error"
        print(f"Free user advanced search response: {response.status_code} - {data}")
    
    def test_advanced_search_works_for_pro_user(self):
        """Pro users should be able to use advanced search"""
        response = requests.get(
            f"{BASE_URL}/api/search/advanced",
            params={"q": "test"},
            headers=self.pro_headers
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "results" in data, "Should have results key"
        assert "search_in" in data, "Should have search_in key"
        print(f"Pro user advanced search response: {data}")


class TestVaultExport:
    """Test vault export endpoint - Pro only"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Create test users"""
        # Free user
        self.free_email = f"test_export_free_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.free_email,
            "password": TEST_PASSWORD,
        })
        self.free_token = response.json()["access_token"]
        self.free_headers = {"Authorization": f"Bearer {self.free_token}"}
        
        # Pro user
        self.pro_email = f"test_export_pro_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.pro_email,
            "password": TEST_PASSWORD,
        })
        self.pro_token = response.json()["access_token"]
        self.pro_headers = {"Authorization": f"Bearer {self.pro_token}"}
        requests.post(f"{BASE_URL}/api/users/upgrade-pro", headers=self.pro_headers)
    
    def test_vault_export_forbidden_for_free_user(self):
        """Free users should get 403 on vault export"""
        response = requests.get(f"{BASE_URL}/api/export/vault", headers=self.free_headers)
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        data = response.json()
        assert "Pro feature" in data.get("detail", ""), "Should mention Pro feature in error"
        print(f"Free user vault export response: {response.status_code} - {data}")
    
    def test_vault_export_works_for_pro_user(self):
        """Pro users should be able to export vault"""
        response = requests.get(f"{BASE_URL}/api/export/vault", headers=self.pro_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "exported_at" in data, "Should have exported_at"
        assert "user" in data, "Should have user info"
        assert "items" in data, "Should have items"
        assert "collections" in data, "Should have collections"
        print(f"Pro user vault export keys: {list(data.keys())}")


class TestSmartReminders:
    """Test smart reminders endpoint - Pro only"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Create test users"""
        # Free user
        self.free_email = f"test_remind_free_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.free_email,
            "password": TEST_PASSWORD,
        })
        self.free_token = response.json()["access_token"]
        self.free_headers = {"Authorization": f"Bearer {self.free_token}"}
        
        # Pro user
        self.pro_email = f"test_remind_pro_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.pro_email,
            "password": TEST_PASSWORD,
        })
        self.pro_token = response.json()["access_token"]
        self.pro_headers = {"Authorization": f"Bearer {self.pro_token}"}
        requests.post(f"{BASE_URL}/api/users/upgrade-pro", headers=self.pro_headers)
    
    def test_reminders_forbidden_for_free_user(self):
        """Free users should get 403 on smart reminders"""
        response = requests.get(f"{BASE_URL}/api/reminders", headers=self.free_headers)
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        data = response.json()
        assert "Pro feature" in data.get("detail", ""), "Should mention Pro feature in error"
        print(f"Free user reminders response: {response.status_code} - {data}")
    
    def test_reminders_works_for_pro_user(self):
        """Pro users should be able to access smart reminders"""
        response = requests.get(f"{BASE_URL}/api/reminders", headers=self.pro_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "reminders" in data, "Should have reminders key"
        assert "total" in data, "Should have total count"
        print(f"Pro user reminders response: {data}")


class TestCollectionLimit:
    """Test collection creation limit - 5 for free users"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Create test users"""
        # Free user
        self.free_email = f"test_coll_free_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.free_email,
            "password": TEST_PASSWORD,
        })
        self.free_token = response.json()["access_token"]
        self.free_headers = {"Authorization": f"Bearer {self.free_token}"}
        
        # Pro user
        self.pro_email = f"test_coll_pro_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.pro_email,
            "password": TEST_PASSWORD,
        })
        self.pro_token = response.json()["access_token"]
        self.pro_headers = {"Authorization": f"Bearer {self.pro_token}"}
        requests.post(f"{BASE_URL}/api/users/upgrade-pro", headers=self.pro_headers)
    
    def test_free_user_limited_to_5_collections(self):
        """Free users should be limited to 5 collections"""
        # Create 5 collections (should work)
        for i in range(5):
            response = requests.post(
                f"{BASE_URL}/api/collections",
                json={"name": f"Test Collection {i+1}"},
                headers=self.free_headers
            )
            assert response.status_code == 200, f"Failed to create collection {i+1}: {response.text}"
            print(f"Created collection {i+1}")
        
        # Try to create 6th collection (should fail with 403)
        response = requests.post(
            f"{BASE_URL}/api/collections",
            json={"name": "Test Collection 6"},
            headers=self.free_headers
        )
        assert response.status_code == 403, f"Expected 403 for 6th collection, got {response.status_code}: {response.text}"
        data = response.json()
        assert "5 collections" in data.get("detail", "").lower() or "limited" in data.get("detail", "").lower()
        print(f"6th collection creation blocked: {data}")
    
    def test_pro_user_unlimited_collections(self):
        """Pro users should be able to create more than 5 collections"""
        # Create 7 collections (should all work for Pro)
        for i in range(7):
            response = requests.post(
                f"{BASE_URL}/api/collections",
                json={"name": f"Pro Collection {i+1}"},
                headers=self.pro_headers
            )
            assert response.status_code == 200, f"Failed to create collection {i+1}: {response.text}"
            print(f"Pro user created collection {i+1}")
        
        print("Pro user successfully created 7 collections")


class TestCancelPro:
    """Test cancel Pro subscription"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Create and upgrade a test user"""
        self.email = f"test_cancel_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": self.email,
            "password": TEST_PASSWORD,
        })
        self.token = response.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        
        # Upgrade to Pro first
        requests.post(f"{BASE_URL}/api/users/upgrade-pro", headers=self.headers)
    
    def test_cancel_pro_subscription(self):
        """Test cancelling Pro subscription returns user to free plan"""
        # Verify user is Pro
        response = requests.get(f"{BASE_URL}/api/users/plan", headers=self.headers)
        assert response.status_code == 200
        data = response.json()
        assert data["is_pro"] == True, "User should be Pro before cancellation"
        print(f"Before cancel: plan_type={data['plan_type']}, is_pro={data['is_pro']}")
        
        # Cancel Pro
        response = requests.post(f"{BASE_URL}/api/users/cancel-pro", headers=self.headers)
        assert response.status_code == 200, f"Cancel failed: {response.text}"
        data = response.json()
        assert data["plan_type"] == "free", "Plan should be free after cancel"
        print(f"Cancel response: {data}")
        
        # Verify user is back to free
        response = requests.get(f"{BASE_URL}/api/users/plan", headers=self.headers)
        assert response.status_code == 200
        data = response.json()
        assert data["is_pro"] == False, "User should not be Pro after cancellation"
        assert data["plan_type"] == "free", "Plan should be free after cancellation"
        print(f"After cancel: plan_type={data['plan_type']}, is_pro={data['is_pro']}")
    
    def test_cancelled_user_loses_pro_features(self):
        """After cancellation, user should lose access to Pro features"""
        # Cancel Pro
        requests.post(f"{BASE_URL}/api/users/cancel-pro", headers=self.headers)
        
        # Try to use Pro features - should get 403
        response = requests.get(f"{BASE_URL}/api/search/advanced", params={"q": "test"}, headers=self.headers)
        assert response.status_code == 403, f"Advanced search should be blocked after cancel: {response.text}"
        
        response = requests.get(f"{BASE_URL}/api/export/vault", headers=self.headers)
        assert response.status_code == 403, f"Vault export should be blocked after cancel: {response.text}"
        
        response = requests.get(f"{BASE_URL}/api/reminders", headers=self.headers)
        assert response.status_code == 403, f"Reminders should be blocked after cancel: {response.text}"
        
        print("Cancelled user correctly blocked from Pro features")


class TestExistingCredentials:
    """Test with provided test credentials"""
    
    def test_free_user_login(self):
        """Test login with free_user@test.com"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "free_user@test.com",
            "password": "testpass123"
        })
        
        # User may not exist, try to register
        if response.status_code == 401:
            print("free_user@test.com does not exist, registering...")
            response = requests.post(f"{BASE_URL}/api/auth/register", json={
                "email": "free_user@test.com",
                "password": "testpass123",
                "name": "Free Test User"
            })
            if response.status_code == 400:  # Already exists but wrong password
                print("User exists but password may differ - skipping test")
                return
        
        if response.status_code == 200:
            token = response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            
            # Check plan
            plan_response = requests.get(f"{BASE_URL}/api/users/plan", headers=headers)
            if plan_response.status_code == 200:
                data = plan_response.json()
                print(f"free_user@test.com plan: {data['plan_type']}, is_pro: {data['is_pro']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
