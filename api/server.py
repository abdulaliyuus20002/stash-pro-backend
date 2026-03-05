from fastapi import FastAPI, APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timedelta
import jwt
from passlib.context import CryptContext
import httpx
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse
from .services.ai_service import (
    generate_summary,
    generate_smart_tags,
    extract_ideas,
    generate_action_items,
    generate_weekly_summary,
    suggest_auto_collection,
)
import asyncio
import random
from fastapi import BackgroundTasks
from fastapi import HTTPException
from passlib.exc import UnknownHashError
from api.db.firebase import FirebaseDB
import hashlib
import stripe
from dotenv import load_dotenv
from pathlib import Path
from fastapi import Request
from playwright.async_api import async_playwright
import yt_dlp

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)


stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MONTHLY_PRICE_ID = os.environ.get("STRIPE_PRO_MONTHLY_PRICE_ID")
YEARLY_PRICE_ID = os.environ.get("STRIPE_PRO_YEARLY_PRICE_ID")

print("MONTHLY:", os.environ.get("STRIPE_SECRET_KEY"))
print("YEARLY:", os.environ.get("STRIPE_PRO_YEARLY_PRICE_ID"))

firebase_db = FirebaseDB()

def require_ai():
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI service not configured"
        )


client = None
db = None

def get_db():
    mongo_url = os.environ.get("MONGO_URL")
    if not mongo_url:
        raise RuntimeError("Database not configured")

    client = AsyncIOMotorClient(mongo_url)
    return client[os.environ.get("DB_NAME", "stash_db")]




# JWT Configuration
SECRET_KEY = os.environ.get('JWT_SECRET', 'stash-secret-key-change-in-production')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7  # 7 days

# Create the main app
app = FastAPI(title="Stash API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Security
security = HTTPBearer()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============== Models ==============

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class NotificationPrefs(BaseModel):
    weekly_review: bool = True
    pending_actions: bool = True
    resurface: bool = True

class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    plan_type: str = "free"
    is_pro: bool = False
    pro_expires_at: Optional[datetime] = None

    push_token: Optional[str] = None
    notifications_enabled: Optional[bool] = False
    notification_prefs: Optional[NotificationPrefs] = None

    created_at: datetime 

    

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

class CheckoutSessionRequest(BaseModel):
    plan: str  # "monthly" | "yearly"

# Plan limits
FREE_PLAN_LIMITS = {
    "max_collections": 10,   # ✅ FREE: 10 collections
    "max_items": 100,        # ✅ FREE: 100 items per collection
    "advanced_search": False,
    "smart_reminders": False,
    "vault_export": False,
    "ai_features": False,
}

PRO_PLAN_LIMITS = {
    "max_collections": -1,  # Unlimited
    "max_items": -1,        # Unlimited
    "advanced_search": True,
    "smart_reminders": True,
    "vault_export": True,
    "ai_features": True,
}



def get_user_limits(user: dict) -> dict:
    """Get limits based on user's plan"""
    if is_pro_user(user):
        return PRO_PLAN_LIMITS
    return FREE_PLAN_LIMITS

def is_pro_user(user: dict) -> bool:
    return user.get("is_pro") or user.get("plan_type") == "pro"


class SavedItemCreate(BaseModel):
    url: str
    title: Optional[str] = None
    thumbnail_url: Optional[str] = None
    platform: Optional[str] = None
    content_type: Optional[str] = None
    notes: Optional[str] = ""
    tags: List[str] = []
    collections: List[str] = []

class SavedItemUpdate(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    collections: Optional[List[str]] = None

class SavedItemResponse(BaseModel):
    id: str
    user_id: str
    url: str
    title: str
    thumbnail_url: Optional[str] = None
    platform: str
    content_type: str
    notes: str = ""
    tags: List[str] = []
    collections: List[str] = []
    created_at: datetime
    ai_summary: Optional[List[str]] = None
    suggested_collection: Optional[str] = None

class CollectionCreate(BaseModel):
    name: str
    is_auto: bool = False

class CollectionUpdate(BaseModel):
    name: str

class CollectionResponse(BaseModel):
    id: str
    user_id: str
    name: str
    item_count: int = 0
    created_at: datetime
    is_auto: bool = False

class MetadataResponse(BaseModel):
    title: Optional[str] = None   # ✅ FIX
    thumbnail_url: Optional[str] = None
    platform: str
    content_type: str
    suggested_tags: List[str] = []

# New models for AI features
class UserPreferences(BaseModel):
    save_types: List[str] = []  # startup_ideas, content_inspiration, etc.
    usage_goals: List[str] = []  # organize_ideas, second_brain, etc.
    onboarding_completed: bool = False

class InsightsResponse(BaseModel):
    total_items: int
    items_this_week: int
    top_platforms: List[Dict[str, Any]]
    top_tags: List[Dict[str, Any]]
    collections_count: int
    weekly_summary: Optional[str] = None
    resurfaced_items: List[Dict[str, Any]] = []

class AISummaryRequest(BaseModel):
    item_id: str

class AutoCollectionSuggestion(BaseModel):
    collection_name: str
    reason: str
    is_new: bool = True
    existing_collection_id: Optional[str] = None

class UpdateProfileRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=20)
    name: Optional[str] = None
    avatar_url: Optional[str] = None

class PushTokenRequest(BaseModel):
    push_token: str



async def run_ai_summary_job(item_id: str, user_id: str):
    db = get_db()
    item = await db.items.find_one(
        {"id": item_id, "user_id": user_id}
    )
    if not item or item.get("ai_summary"):
        return

    summary = await generate_summary(
        title=item["title"],
        platform=item["platform"],
        url=item["url"],
    )

    if summary:
        await db.items.update_one(
            {"id": item_id},
            {"$set": {"ai_summary": summary}}
        )


# ============== Auth Helpers ==============

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    # bcrypt handles bytes internally
    return pwd_context.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)

def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def require_pro(user: dict):
    if not is_pro_user(user):
        raise HTTPException(
            status_code=403,
            detail={
                "message": "This feature is available on Pro",
                "upgrade_required": True,
                "feature": "ai_features",
                "cta": "Upgrade to Pro to unlock AI features"
            }
        )

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        user = await firebase_db.get_user_by_id(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except (jwt.PyJWTError, jwt.DecodeError, jwt.InvalidTokenError):
        raise HTTPException(status_code=401, detail="Invalid token")

# ============== URL Metadata Extraction ==============

def detect_platform(url: str) -> tuple:
    """Detect platform and content type from URL"""
    domain = urlparse(url).netloc.lower()
    
    platform_map = {
        'youtube.com': ('YouTube', 'video'),
        'youtu.be': ('YouTube', 'video'),
        'twitter.com': ('X', 'post'),
        'x.com': ('X', 'post'),
        'instagram.com': ('Instagram', 'post'),
        'tiktok.com': ('TikTok', 'video'),
        'linkedin.com': ('LinkedIn', 'post'),
        'medium.com': ('Medium', 'article'),
        'reddit.com': ('Reddit', 'post'),
        'github.com': ('GitHub', 'article'),
        'substack.com': ('Substack', 'article'),
    }
    
    for key, value in platform_map.items():
        if key in domain:
            return value
    
    return ('Web', 'article')

def extract_suggested_tags(title: str) -> List[str]:
    """Extract suggested tags from title keywords"""
    if not title:
        return []
    
    # Common stop words to filter out
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'it', 'this', 'that', 'how', 'what', 'why', 'when', 'where', 'who'}
    
    # Extract words and filter
    words = re.findall(r'\b[a-zA-Z]{3,}\b', title.lower())
    tags = [word for word in words if word not in stop_words][:5]
    
    return list(set(tags))



async def resolve_url(url: str) -> str:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36"
            }
        ) as client:

            r = await client.get(url)
            return str(r.url)

    except Exception:
        return url

def extract_youtube_video_id(url: str):
    patterns = [
        r"youtu\.be/([^?&]+)",
        r"youtube\.com/watch\?v=([^&]+)",
        r"youtube\.com/shorts/([^?&]+)"
    ]

    for p in patterns:
        match = re.search(p, url)
        if match:
            return match.group(1)

    return None

async def handle_youtube(url: str):

    try:

        ydl_opts = {
            "quiet": True,
            "skip_download": True,
        }

        loop = asyncio.get_event_loop()

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(
                None,
                lambda: ydl.extract_info(url, download=False)
            )

        return {
            "title": info.get("title") or "YouTube Video",
            "thumbnail_url": info.get("thumbnail"),
            "platform": "YouTube",
            "content_type": "video",
            "suggested_tags": ["youtube", "video"]
        }

    except Exception as e:
        logger.error(f"YouTube extraction failed: {e}")

        # ✅ Fallback: scrape title from page
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url)

            soup = BeautifulSoup(r.text, "html.parser")

            title = soup.title.string if soup.title else "YouTube Video"

            return {
                "title": title.replace(" - YouTube", ""),
                "thumbnail_url": None,
                "platform": "YouTube",
                "content_type": "video",
                "suggested_tags": ["youtube"]
            }

        except Exception:
            return {
                "title": "YouTube Video",
                "thumbnail_url": None,
                "platform": "YouTube",
                "content_type": "video",
                "suggested_tags": ["youtube"]
            }

async def handle_tiktok(url: str):

    try:

        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "nocheckcertificate": True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get("title")
        thumbnail = info.get("thumbnail")

        if not title:
            title = "TikTok Video"

        return {
            "title": title,
            "thumbnail_url": thumbnail,
            "platform": "TikTok",
            "content_type": "video",
            "suggested_tags": ["tiktok", "video"]
        }

    except Exception as e:
        logger.error(f"TikTok extraction failed: {e}")
        return None



async def fetch_metadata_browser(url: str):

    try:
        async with async_playwright() as p:

            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )

            context = await browser.new_context()

            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=45000)

            try:
                await page.wait_for_selector('meta[property="og:title"]', timeout=5000)
            except:
                pass

            title = await page.evaluate("""
                document.querySelector('meta[property="og:title"]')?.content
                || document.querySelector('meta[name="twitter:title"]')?.content
                || document.title
            """)

            description = await page.evaluate("""
                document.querySelector('meta[property="og:description"]')?.content
                || document.querySelector('meta[name="twitter:description"]')?.content
                || document.querySelector('meta[name="description"]')?.content
            """)

            image = await page.evaluate("""
                document.querySelector('meta[property="og:image"]')?.content
                || document.querySelector('meta[name="twitter:image"]')?.content
            """)

            site_name = await page.evaluate("""
                document.querySelector('meta[property="og:site_name"]')?.content
            """)

            await browser.close()

            return {
                "title": title,
                "thumbnail_url": image,
                "description": description,
                "site_name": site_name
            }

    except Exception as e:
        logger.error(f"Browser metadata extraction failed: {e}")
        return None


async def fetch_url_metadata(url: str):

    url = await resolve_url(url)

    platform, content_type = detect_platform(url)

    title = None
    image = None

    # 1️⃣ Special handler
    if platform == "YouTube":
        yt = await handle_youtube(url)
        if yt:
            return yt

    if platform == "TikTok":
        tk = await handle_tiktok(url)
        if tk:
            return tk

    

    # 2️⃣ Fast metadata extraction
    static_data = await fetch_metadata_static(url)

    if static_data:
        title = static_data.get("title")
        image = static_data.get("thumbnail_url")

    # 3️⃣ Browser fallback
    if (not title or not image) and platform not in ["YouTube", "TikTok", "X"]:

        browser_data = await fetch_metadata_browser(url)

        if browser_data:
            title = title or browser_data.get("title")
            image = image or browser_data.get("thumbnail_url")

    # 4️⃣ Final fallback
    if not title:
        title = urlparse(url).netloc

    tags = extract_suggested_tags(title)

    return {
        "title": title,
        "thumbnail_url": image,
        "platform": platform,
        "content_type": content_type,
        "suggested_tags": tags
    }

async def fetch_metadata_static(url: str):
    """
    Fast metadata extraction using OpenGraph and Twitter tags.
    Works for most websites without a browser.
    """

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122 Safari/537.36"
        }

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        def get_meta(property_name):
            tag = soup.find("meta", property=property_name)
            if tag and tag.get("content"):
                return tag["content"]
            return None

        def get_name(name):
            tag = soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"]
            return None

        title = (
            get_meta("og:title")
            or get_name("twitter:title")
            or (soup.title.string if soup.title else None)
        )

        description = (
            get_meta("og:description")
            or get_name("twitter:description")
            or get_name("description")
        )

        image = (
            get_meta("og:image")
            or get_name("twitter:image")
        )

        site_name = get_meta("og:site_name")

        return {
            "title": title,
            "thumbnail_url": image,
            "description": description,
            "site_name": site_name
        }

    except Exception as e:
        logger.error(f"Metadata extraction failed: {e}")
        return None

# ============== Auth Routes ==============

@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserCreate):
    # Check if user exists
    existing = await firebase_db.get_user_by_email(user_data.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    user_id = str(uuid.uuid4())
    user = {
        "id": user_id,
        "email": user_data.email.lower(),
        "password": hash_password(user_data.password),
        "name": user_data.name or user_data.email.split('@')[0],
        "plan_type": "free",
        "created_at": datetime.utcnow()
    }
    await firebase_db.create_user(user)
    
    token = create_access_token(user_id)
    
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user["id"],
            email=user["email"],
            name=user["name"],
            plan_type=user["plan_type"],
            created_at=user["created_at"]
        )
    )

@api_router.post("/auth/login", response_model=TokenResponse)
async def login(credentials: UserLogin):
    user = await firebase_db.get_user_by_email(credentials.email)
    
    if not user or not user.get("password"):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(credentials.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    token = create_access_token(user["id"])
    
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user["id"],
            email=user["email"],
            name=user.get("name"),
            username=user.get("username"),
            avatar_url=user.get("avatar_url"),
            plan_type=user.get("plan_type", "free"),
            created_at=user["created_at"]
        )

    )


@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        name=current_user.get("name"),
        username=current_user.get("username"),
        avatar_url=current_user.get("avatar_url"),
        plan_type=current_user.get("plan_type", "free"),
        is_pro=current_user.get("is_pro", False),
        pro_expires_at=current_user.get("pro_expires_at"),
        push_token=current_user.get("push_token"),
        notifications_enabled=current_user.get("notifications_enabled", False),
        notification_prefs=current_user.get("notification_prefs"),
        created_at=current_user["created_at"]
    )



# ============== URL Metadata Route ==============

@api_router.post("/extract-metadata", response_model=MetadataResponse)
async def extract_metadata(data: dict, current_user: dict = Depends(get_current_user)):
    url = data.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    metadata = await fetch_url_metadata(url)
    return MetadataResponse(**metadata)

# ============== Saved Items Routes ==============

@api_router.post("/items", response_model=SavedItemResponse)
async def create_item(
    item_data: SavedItemCreate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()  # ✅ FIX: always initialize db first

    # Fetch metadata if not provided
    if not item_data.title or not item_data.platform:
        metadata = await fetch_url_metadata(item_data.url)
        if not item_data.title:
            item_data.title = metadata["title"]
        if not item_data.platform:
            item_data.platform = metadata["platform"]
        if not item_data.content_type:
            item_data.content_type = metadata["content_type"]
        if not item_data.thumbnail_url:
            item_data.thumbnail_url = metadata["thumbnail_url"]

    item_id = str(uuid.uuid4())
    item = {
        "id": item_id,
        "user_id": current_user["id"],
        "url": item_data.url,
        "title": item_data.title or item_data.url,
        "thumbnail_url": item_data.thumbnail_url,
        "platform": item_data.platform or "Web",
        "content_type": item_data.content_type or "article",
        "notes": item_data.notes or "",
        "tags": item_data.tags or [],
        "collections": item_data.collections or [],
        "created_at": datetime.utcnow(),
    }

    limits = get_user_limits(current_user)

    if limits["max_items"] != -1 and item_data.collections:
        for collection_id in item_data.collections:
            count = await db.items.count_documents({
                "user_id": current_user["id"],
                "collections": collection_id
            })

            if count >= limits["max_items"]:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "message": "Collection item limit reached",
                        "upgrade_required": True,
                        "feature": "items_per_collection",
                        "limit": limits["max_items"],
                        "cta": "Upgrade to Pro to save unlimited items"
                    }
                )

    await db.items.insert_one(item)

    # ================= AUTO COLLECTION (PRO ONLY) =================
    if is_pro_user(current_user):
        try:
            collections = await db.collections.find(
                {"user_id": current_user["id"]}
            ).to_list(50)

            suggestion = await suggest_auto_collection(
                title=item["title"],
                platform=item["platform"],
                existing_collections=[c["name"] for c in collections],
            )

            if suggestion:
                collection_id = None

                if not suggestion["is_new"]:
                    existing = next(
                        (c for c in collections if c["name"] == suggestion["collection_name"]),
                        None
                    )
                    if existing:
                        collection_id = existing["id"]
                else:
                    collection_id = str(uuid.uuid4())
                    await db.collections.insert_one({
                        "id": collection_id,
                        "user_id": current_user["id"],
                        "name": suggestion["collection_name"],
                        "created_at": datetime.utcnow(),
                        "is_auto": True,
                    })

                if collection_id:
                    await db.items.update_one(
                        {"id": item_id},
                        {"$set": {"collections": [collection_id]}}
                    )
                    item["collections"] = [collection_id]

        except Exception as e:
            logger.warning(f"Auto-collection failed: {e}")

    return SavedItemResponse(**item)

@api_router.get("/items", response_model=List[SavedItemResponse])
async def get_items(
    sort: str = "newest",
    platform: Optional[str] = None,
    collection: Optional[str] = None,
    tag: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    query = {"user_id": current_user["id"]}
    
    if platform:
        query["platform"] = platform
    if collection:
        query["collections"] = collection
    if tag:
        query["tags"] = tag
    
    sort_order = -1 if sort == "newest" else 1
    db = get_db()
    items = await db.items.find(query).sort("created_at", sort_order).to_list(1000)
    return [SavedItemResponse(**item) for item in items]

@api_router.get("/items/{item_id}", response_model=SavedItemResponse)
async def get_item(item_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    item = await db.items.find_one({"id": item_id, "user_id": current_user["id"]})
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return SavedItemResponse(**item)

@api_router.put("/items/{item_id}", response_model=SavedItemResponse)
async def update_item(item_id: str, update_data: SavedItemUpdate, current_user: dict = Depends(get_current_user)):
    db = get_db()
    item = await db.items.find_one({"id": item_id, "user_id": current_user["id"]})
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    update_dict = {k: v for k, v in update_data.dict().items() if v is not None}
    
    if update_dict:
        await db.items.update_one({"id": item_id}, {"$set": update_dict})
    
    updated_item = await db.items.find_one({"id": item_id})
    return SavedItemResponse(**updated_item)

@api_router.delete("/items/{item_id}")
async def delete_item(item_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    result = await db.items.delete_one({"id": item_id, "user_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"message": "Item deleted"}

# ============== Collections Routes ==============

@api_router.post("/collections", response_model=CollectionResponse)
async def create_collection(
    data: CollectionCreate,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    limits = get_user_limits(current_user)

    if limits["max_collections"] != -1:
        count = await db.collections.count_documents(
            {"user_id": current_user["id"]}
        )
        if count >= limits["max_collections"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Collection limit reached",
                    "upgrade_required": True,
                    "feature": "collections",
                    "limit": limits["max_collections"],
                    "cta": "Upgrade to Pro for unlimited collections"
                }
            )

    collection = {
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "name": data.name,
        "created_at": datetime.utcnow(),
        "is_auto": data.is_auto,
    }

    await db.collections.insert_one(collection)
    return CollectionResponse(**collection, item_count=0)

@api_router.get("/collections", response_model=List[CollectionResponse])
async def get_collections(current_user: dict = Depends(get_current_user)):
    db = get_db()
    collections = await db.collections.find({"user_id": current_user["id"]}).to_list(100)
    
    result = []
    for col in collections:
        item_count = await db.items.count_documents({
            "user_id": current_user["id"],
            "collections": col["id"]
        })
        result.append(CollectionResponse(**col, item_count=item_count))
    
    return result

@api_router.put("/collections/{collection_id}", response_model=CollectionResponse)
async def update_collection(collection_id: str, data: CollectionUpdate, current_user: dict = Depends(get_current_user)):
    db = get_db()
    collection = await db.collections.find_one({"id": collection_id, "user_id": current_user["id"]})
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    
    
    await db.collections.update_one({"id": collection_id}, {"$set": {"name": data.name}})
    
    
    updated = await db.collections.find_one({"id": collection_id})
    item_count = await db.items.count_documents({
        "user_id": current_user["id"],
        "collections": collection_id
    })
    
    return CollectionResponse(**updated, item_count=item_count)

@api_router.delete("/collections/{collection_id}")
async def delete_collection(collection_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    result = await db.collections.delete_one({"id": collection_id, "user_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Collection not found")
    
    # Remove collection from all items
    
    await db.items.update_many(
        {"user_id": current_user["id"], "collections": collection_id},
        {"$pull": {"collections": collection_id}}
    )
    
    return {"message": "Collection deleted"}

# ============== Search Route ==============

@api_router.get("/search", response_model=List[SavedItemResponse])
async def search_items(q: str, current_user: dict = Depends(get_current_user)):
    if not q or len(q) < 2:
        return []
    
    # Create text search query
    query = {
        "user_id": current_user["id"],
        "$or": [
            {"title": {"$regex": q, "$options": "i"}},
            {"notes": {"$regex": q, "$options": "i"}},
            {"tags": {"$regex": q, "$options": "i"}},
            {"platform": {"$regex": q, "$options": "i"}},
            {"url": {"$regex": q, "$options": "i"}}
        ]
    }
    db = get_db()
    items = await db.items.find(query).sort("created_at", -1).to_list(100)
    return [SavedItemResponse(**item) for item in items]

# ============== Tags Route ==============

@api_router.get("/tags", response_model=List[str])
async def get_all_tags(current_user: dict = Depends(get_current_user)):
    """Get all unique tags for the user"""
    pipeline = [
        {"$match": {"user_id": current_user["id"]}},
        {"$unwind": "$tags"},
        {"$group": {"_id": "$tags"}},
        {"$sort": {"_id": 1}}
    ]
    
    db = get_db()
    result = await db.items.aggregate(pipeline).to_list(100)
    return [r["_id"] for r in result]

# ============== AI Features ==============

# Platform to auto-collection mapping
PLATFORM_COLLECTIONS = {
    'YouTube': 'Videos',
    'TikTok': 'Videos',
    'Instagram': 'Social',
    'X': 'Social',
    'LinkedIn': 'Professional',
    'Medium': 'Articles',
    'Substack': 'Articles',
    'Reddit': 'Discussions',
    'GitHub': 'Tech & Code',
    'Web': 'Web Saves'
}

# async def generate_ai_summary(title: str, url: str, platform: str) -> List[str]:
#     """Generate AI summary using GPT-4"""
#     try:
#         api_key = os.environ.get('EMERGENT_LLM_KEY')
#         if not api_key:
#             return []
        
#         chat = LlmChat(
#             api_key=api_key,
#             session_id=f"summary-{uuid.uuid4()}",
#             system_message="You are a helpful assistant that creates concise bullet-point summaries. Always respond with exactly 3-5 bullet points, each starting with '•'. Keep each point under 15 words."
#         ).with_model("openai", "gpt-4")
        
#         user_message = UserMessage(
#             text=f"Create a brief summary of this saved content:\nTitle: {title}\nPlatform: {platform}\nURL: {url}\n\nProvide 3-5 key bullet points about what this content likely covers based on the title."
#         )
        
#         response = await chat.send_message(user_message)
        
#         # Parse bullet points from response
#         lines = response.strip().split('\n')
#         bullets = []
#         for line in lines:
#             line = line.strip()
#             if line.startswith('•') or line.startswith('-') or line.startswith('*'):
#                 bullet = line.lstrip('•-* ').strip()
#                 if bullet:
#                     bullets.append(bullet)
        
#         return bullets[:5] if bullets else []
#     except Exception as e:
#         logger.error(f"AI summary error: {e}")
#         return []

# async def suggest_auto_collection(title: str, platform: str, user_id: str) -> Optional[Dict]:
#     """Suggest auto-collection based on platform and AI analysis"""
#     try:
#         # First try platform-based suggestion
#         platform_collection = PLATFORM_COLLECTIONS.get(platform, 'General')
        
#         # Check if user has this collection
#         existing = await db.collections.find_one({
#             "user_id": user_id,
#             "name": platform_collection
#         })
        
#         if existing:
#             return {
#                 "collection_name": platform_collection,
#                 "reason": f"Based on {platform} content",
#                 "is_new": False,
#                 "existing_collection_id": existing["id"]
#             }
        
#         # Try AI-based suggestion for more specific categorization
#         api_key = os.environ.get('EMERGENT_LLM_KEY')
#         if api_key:
#             chat = LlmChat(
#                 api_key=api_key,
#                 session_id=f"collection-{uuid.uuid4()}",
#                 system_message="You are a content organizer. Suggest ONE collection name (2-3 words max) for organizing content. Just respond with the collection name, nothing else."
#             ).with_model("openai", "gpt-4")
            
#             user_message = UserMessage(
#                 text=f"Suggest a collection name for: '{title}' from {platform}"
#             )
            
#             response = await chat.send_message(user_message)
#             ai_suggestion = response.strip().strip('"\'')
            
#             if ai_suggestion and len(ai_suggestion) < 30:
#                 # Check if this collection exists
#                 existing_ai = await db.collections.find_one({
#                     "user_id": user_id,
#                     "name": {"$regex": f"^{ai_suggestion}$", "$options": "i"}
#                 })
                
#                 if existing_ai:
#                     return {
#                         "collection_name": existing_ai["name"],
#                         "reason": f"AI suggested based on content",
#                         "is_new": False,
#                         "existing_collection_id": existing_ai["id"]
#                     }
                
#                 return {
#                     "collection_name": ai_suggestion,
#                     "reason": f"AI suggested based on content",
#                     "is_new": True,
#                     "existing_collection_id": None
#                 }
        
#         return {
#             "collection_name": platform_collection,
#             "reason": f"Based on {platform} content",
#             "is_new": True,
#             "existing_collection_id": None
#         }
#     except Exception as e:
#         logger.error(f"Auto-collection suggestion error: {e}")
#         return None

# async def generate_weekly_summary(user_id: str, items: List[dict]) -> Optional[str]:
#     """Generate weekly digest summary"""
#     try:
#         if not items:
#             return None
        
#         api_key = os.environ.get('EMERGENT_LLM_KEY')
#         if not api_key:
#             return None
        
#         # Prepare items summary
#         items_text = "\n".join([f"- {item.get('title', 'Untitled')} ({item.get('platform', 'Web')})" for item in items[:10]])
        
#         chat = LlmChat(
#             api_key=api_key,
#             session_id=f"digest-{uuid.uuid4()}",
#             system_message="You are a helpful assistant that creates brief, encouraging weekly summaries. Keep it under 50 words, friendly and motivational."
#         ).with_model("openai", "gpt-4")
        
#         user_message = UserMessage(
#             text=f"Create a brief weekly summary for someone who saved these items:\n{items_text}\n\nMention the themes and encourage them to review their saves."
#         )
        
#         response = await chat.send_message(user_message)
#         return response.strip()
#     except Exception as e:
#         logger.error(f"Weekly summary error: {e}")
#         return None

# ============== PREMIUM AI FEATURES ==============

# async def extract_ideas(title: str, url: str, platform: str) -> List[Dict[str, str]]:
#     """Extract key ideas and insights from content - PREMIUM FEATURE"""
#     try:
#         api_key = os.environ.get('EMERGENT_LLM_KEY')
#         if not api_key:
#             return []
        
#         chat = LlmChat(
#             api_key=api_key,
#             session_id=f"ideas-{uuid.uuid4()}",
#             system_message="""You are an expert idea extractor. Analyze content and extract 3-5 key ideas or insights.
# For each idea, provide:
# 1. A short title (3-5 words)
# 2. A brief description (1 sentence)
# 3. A category: "concept", "insight", "strategy", "quote", or "takeaway"

# Format your response as:
# IDEA: [title]
# DESC: [description]
# TYPE: [category]

# Repeat for each idea."""
#         ).with_model("openai", "gpt-4")
        
#         user_message = UserMessage(
#             text=f"Extract key ideas from this content:\nTitle: {title}\nPlatform: {platform}\nURL: {url}"
#         )
        
#         response = await chat.send_message(user_message)
        
#         # Parse ideas from response
#         ideas = []
#         current_idea = {}
        
#         for line in response.strip().split('\n'):
#             line = line.strip()
#             if line.startswith('IDEA:'):
#                 if current_idea:
#                     ideas.append(current_idea)
#                 current_idea = {"title": line[5:].strip()}
#             elif line.startswith('DESC:'):
#                 current_idea["description"] = line[5:].strip()
#             elif line.startswith('TYPE:'):
#                 current_idea["type"] = line[5:].strip().lower()
        
#         if current_idea and "title" in current_idea:
#             ideas.append(current_idea)
        
#         return ideas[:5]
#     except Exception as e:
#         logger.error(f"Idea extraction error: {e}")
#         return []



# async def generate_action_items(title: str, url: str, platform: str, notes: str = "") -> List[Dict[str, Any]]:
#     """Turn saved content into actionable tasks - PREMIUM FEATURE"""
#     try:
#         api_key = os.environ.get('EMERGENT_LLM_KEY')
#         if not api_key:
#             return []
        
#         chat = LlmChat(
#             api_key=api_key,
#             session_id=f"actions-{uuid.uuid4()}",
#             system_message="""You are a productivity expert. Convert saved content into 3-5 specific, actionable tasks.
# Each action should be:
# - Concrete and specific
# - Achievable in a reasonable timeframe
# - Relevant to the content

# Format each action as:
# ACTION: [task description]
# PRIORITY: [high/medium/low]
# TIME: [estimated time: 5min/15min/30min/1hr/2hr+]
# CATEGORY: [learn/create/share/implement/review]"""
#         ).with_model("openai", "gpt-4")
        
#         notes_str = f"\nUser notes: {notes}" if notes else ""
        
#         user_message = UserMessage(
#             text=f"Generate action items from this saved content:\nTitle: {title}\nPlatform: {platform}\nURL: {url}{notes_str}"
#         )
        
#         response = await chat.send_message(user_message)
        
#         # Parse action items
#         actions = []
#         current_action = {}
        
#         for line in response.strip().split('\n'):
#             line = line.strip()
#             if line.startswith('ACTION:'):
#                 if current_action and "task" in current_action:
#                     actions.append(current_action)
#                 current_action = {"task": line[7:].strip(), "completed": False}
#             elif line.startswith('PRIORITY:'):
#                 current_action["priority"] = line[9:].strip().lower()
#             elif line.startswith('TIME:'):
#                 current_action["estimated_time"] = line[5:].strip()
#             elif line.startswith('CATEGORY:'):
#                 current_action["category"] = line[9:].strip().lower()
        
#         if current_action and "task" in current_action:
#             actions.append(current_action)
        
#         return actions[:5]
#     except Exception as e:
#         logger.error(f"Action items error: {e}")
#         return []

# ============== AI Endpoints ==============

# @api_router.post("/items/{item_id}/ai-summary")
# async def generate_item_summary(item_id: str, current_user: dict = Depends(get_current_user)):
#     """Generate AI summary for an item"""
#     item = await db.items.find_one({"id": item_id, "user_id": current_user["id"]})
#     if not item:
#         raise HTTPException(status_code=404, detail="Item not found")
    
#     summary = await generate_ai_summary(item.get("title", ""), item.get("url", ""), item.get("platform", "Web"))
    
#     if summary:
#         await db.items.update_one({"id": item_id}, {"$set": {"ai_summary": summary}})
    
#     return {"summary": summary}

@api_router.post("/items/{item_id}/extract-ideas")
async def extract_item_ideas(item_id: str, current_user: dict = Depends(get_current_user)):
    """Extract key ideas from an item - PREMIUM FEATURE"""
    db = get_db()
    item = await db.items.find_one({"id": item_id, "user_id": current_user["id"]})
    require_ai()
    require_pro(current_user)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    ideas = await extract_ideas(item.get("title", ""), item.get("url", ""), item.get("platform", "Web"))
    
    if ideas:
        
        await db.items.update_one({"id": item_id}, {"$set": {"extracted_ideas": ideas}})
    
    return {"ideas": ideas}

@api_router.post("/items/{item_id}/smart-tags")
async def generate_item_smart_tags(item_id: str, current_user: dict = Depends(get_current_user)):
    """Generate smart tag suggestions - PREMIUM FEATURE"""
    db = get_db()
    item = await db.items.find_one({"id": item_id, "user_id": current_user["id"]})
    require_ai()
    require_pro(current_user)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    tags = await generate_smart_tags(
        item.get("title", ""), 
        item.get("url", ""), 
        item.get("platform", "Web"),
        item.get("tags", [])
    )
    
    return {"suggested_tags": tags}

@api_router.post("/items/{item_id}/action-items")
async def generate_item_actions(item_id: str, current_user: dict = Depends(get_current_user)):
    require_ai()
    require_pro(current_user)

    if not OPENAI_API_KEY:
        return {"action_items": []}

    db = get_db()
    item = await db.items.find_one(
        {"id": item_id, "user_id": current_user["id"]}
    )

    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    actions = await generate_action_items(
        title=item.get("title", ""),
        platform=item.get("platform", "Web"),
        notes=item.get("notes", ""),
    )

    if actions:
        await db.items.update_one(
            {"id": item_id},
            {"$set": {"action_items": actions}}
        )

    return {"action_items": actions}

@api_router.put("/items/{item_id}/action-items/{action_index}/toggle")
async def toggle_action_item(item_id: str, action_index: int, current_user: dict = Depends(get_current_user)):
    """Toggle action item completion status"""
    db = get_db()
    item = await db.items.find_one({"id": item_id, "user_id": current_user["id"]})
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    action_items = item.get("action_items", [])
    if action_index < 0 or action_index >= len(action_items):
        raise HTTPException(status_code=400, detail="Invalid action index")
    
    action_items[action_index]["completed"] = not action_items[action_index].get("completed", False)
    
    await db.items.update_one({"id": item_id}, {"$set": {"action_items": action_items}})
    
    return {"action_items": action_items}

@api_router.post("/items/{item_id}/apply-smart-tag")
async def apply_smart_tag(item_id: str, tag_name: str, current_user: dict = Depends(get_current_user)):
    """Apply a suggested smart tag to an item"""
    db = get_db()
    item = await db.items.find_one({"id": item_id, "user_id": current_user["id"]})
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    current_tags = item.get("tags", [])
    if tag_name not in current_tags:
        current_tags.append(tag_name)
        
        await db.items.update_one({"id": item_id}, {"$set": {"tags": current_tags}})
    
    return {"tags": current_tags}

@api_router.get("/items/{item_id}/suggest-collection")
async def get_collection_suggestion(
    item_id: str,
    current_user: dict = Depends(get_current_user),
):
    require_ai()
    require_pro(current_user)

    db = get_db()
    item = await db.items.find_one({"id": item_id, "user_id": current_user["id"]})
    if not item:
        raise HTTPException(404, "Item not found")

    
    
    collections = await db.collections.find(
        {"user_id": current_user["id"]}
    ).to_list(50)

    result = await suggest_auto_collection(
        item["title"],
        item["platform"],
        [c["name"] for c in collections],
    )

    return result or {
        "collection_name": "General",
        "reason": "Fallback",
        "is_new": True,
    }

@api_router.get("/insights", response_model=InsightsResponse)
async def get_insights(current_user: dict = Depends(get_current_user)):
    """Get user insights and weekly digest"""
    user_id = current_user["id"]
    
    # Total items
    db = get_db()
    total_items = await db.items.count_documents({"user_id": user_id})
    
    # Items this week
    week_ago = datetime.utcnow() - timedelta(days=7)
    
    items_this_week = await db.items.count_documents({
        "user_id": user_id,
        "created_at": {"$gte": week_ago}
    })
    
    # Top platforms
    platform_pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$platform", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 5}
    ]
    
    top_platforms = await db.items.aggregate(platform_pipeline).to_list(5)
    
    # Top tags
    tags_pipeline = [
        {"$match": {"user_id": user_id}},
        {"$unwind": "$tags"},
        {"$group": {"_id": "$tags", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 5}
    ]
    
    top_tags = await db.items.aggregate(tags_pipeline).to_list(5)
    
    # Collections count
    
    collections_count = await db.collections.count_documents({"user_id": user_id})
    
    # Resurfaced items (saved 30+ days ago)
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    old_items = await db.items.find({
        "user_id": user_id,
        "created_at": {"$lte": thirty_days_ago}
    }).sort("created_at", 1).limit(3).to_list(3)
    
    resurfaced = []
    for item in old_items:
        days_ago = (datetime.utcnow() - item["created_at"]).days
        resurfaced.append({
            "id": item["id"],
            "title": item.get("title", "Untitled"),
            "thumbnail_url": item.get("thumbnail_url"),
            "platform": item.get("platform", "Web"),
            "days_ago": days_ago,
            "message": f"You saved this {days_ago} days ago"
        })
    
    
    weekly_items = await db.items.find(
        {"user_id": user_id, "created_at": {"$gte": week_ago}}
    ).to_list(10)

    weekly_summary = (
        await generate_weekly_summary(weekly_items)
        if current_user.get("is_pro")
        else None
    )
    
    return InsightsResponse(
        total_items=total_items,
        items_this_week=items_this_week,
        top_platforms=[{"platform": p["_id"], "count": p["count"]} for p in top_platforms],
        top_tags=[{"tag": t["_id"], "count": t["count"]} for t in top_tags],
        collections_count=collections_count,
        weekly_summary=weekly_summary,
        resurfaced_items=resurfaced
    )

@api_router.get("/resurfaced")
async def get_resurfaced_items(current_user: dict = Depends(get_current_user)):
    """Get items to resurface (saved 30+ days ago)"""
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    db = get_db()
    items = await db.items.find({
        "user_id": current_user["id"],
        "created_at": {"$lte": thirty_days_ago}
    }).sort("created_at", 1).limit(5).to_list(5)
    
    result = []
    for item in items:
        days_ago = (datetime.utcnow() - item["created_at"]).days
        result.append({
            **SavedItemResponse(**item).dict(),
            "days_ago": days_ago,
            "resurface_message": f"You saved this {days_ago} days ago"
        })
    
    return result

# ============User Settings===========
@api_router.put("/users/change-password")
async def change_password(
    data: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user)
):
    if not verify_password(data.current_password, current_user["password"]):
        raise HTTPException(
            status_code=400,
            detail="Current password is incorrect"
        )

    if verify_password(data.new_password, current_user["password"]):
        raise HTTPException(
            status_code=400,
            detail="New password must be different from current password"
        )

    new_hashed = hash_password(data.new_password)

    await firebase_db.update_user(
    current_user["id"],
    {"password": new_hashed}
)

    return {"message": "Password updated successfully"}


@api_router.delete("/users/me")
async def delete_account(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]

    # Delete user-related data
    db = get_db()
    await db.items.delete_many({"user_id": user_id})
    
    await db.collections.delete_many({"user_id": user_id})

    # Delete user
    
    result = await firebase_db.delete_user(user_id)

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"message": "Account deleted successfully"}


@api_router.put("/users/profile")
async def update_profile(
    data: UpdateProfileRequest,
    current_user: dict = Depends(get_current_user)
):
    update_data = {}

    if data.username:
        update_data["username"] = data.username.lower()

    if data.name is not None:
        update_data["name"] = data.name

    if data.avatar_url is not None:
        update_data["avatar_url"] = data.avatar_url

    if not update_data:
        return {"message": "No changes provided"}

    await firebase_db.update_user(current_user["id"], update_data)

    updated_user = await firebase_db.get_user_by_id(current_user["id"])

    return {
        "message": "Profile updated",
        "user": {
            "id": updated_user["id"],
            "email": updated_user["email"],
            "name": updated_user.get("name"),
            "username": updated_user.get("username"),
            "avatar_url": updated_user.get("avatar_url"),
            "plan_type": updated_user.get("plan_type"),
            "created_at": updated_user["created_at"],
        }
    }



@api_router.post("/users/push-token")
async def save_push_token(
    data: PushTokenRequest,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    await firebase_db.update_user(
        current_user["id"],
        {
            "push_token": data.push_token,
            "notifications_enabled": True,
            "notification_prefs": {
                "weekly_review": True,
                "pending_actions": True,
                "resurface": True
            }
        }
    )
    return {"message": "Push token saved"}


@api_router.get("/users/notifications")
async def get_notification_settings(current_user: dict = Depends(get_current_user)):
    return {
        "notifications_enabled": current_user.get("notifications_enabled", False),
        "notification_prefs": current_user.get("notification_prefs", {
            "weekly_review": True,
            "pending_actions": True,
            "resurface": True
        })
    }








# ============== User Preferences ==============

@api_router.put("/users/preferences")
async def update_preferences(preferences: UserPreferences, current_user: dict = Depends(get_current_user)):
    """Update user preferences from onboarding"""
    db = get_db()
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {
            "preferences": preferences.dict(),
            "onboarding_completed": preferences.onboarding_completed
        }}
    )
    return {"message": "Preferences updated"}

@api_router.get("/users/preferences")
async def get_preferences(current_user: dict = Depends(get_current_user)):
    db = get_db()

    user_doc = await db.users.find_one({"id": current_user["id"]}) or {}

    return user_doc.get("preferences", {
        "save_types": [],
        "usage_goals": [],
        "onboarding_completed": False
    })

# ============== Pro Subscription ==============

@api_router.get("/users/plan")
async def get_user_plan(current_user: dict = Depends(get_current_user)):
    """Get user's current plan and limits"""
    db = get_db()
    is_pro = (
        current_user.get("is_pro", False)
        or current_user.get("plan_type") == "pro"
    )
    
    # Count current usage
    
    items_count = await db.items.count_documents({"user_id": current_user["id"]})
    collections_count = await db.collections.count_documents({"user_id": current_user["id"]})
    
    limits = get_user_limits(current_user)
    
    return {
        "plan_type": "pro" if is_pro else "free",
        "is_pro": is_pro,
        "pro_expires_at": current_user.get("pro_expires_at"),
        "limits": limits,
        "usage": {
            "items_count": items_count,
            "collections_count": collections_count,
            "items_limit": limits["max_items"],
            "collections_limit": limits["max_collections"],
        },
        "features": {
            "unlimited_collections": is_pro,
            "advanced_search": is_pro,
            "smart_reminders": is_pro,
            "vault_export": is_pro,
            "ai_features": is_pro,
        }
    }

# @api_router.post("/users/upgrade-pro")
# async def upgrade_to_pro(current_user: dict = Depends(get_current_user)):
#     """Upgrade user to Pro plan (simulated - in production use payment provider)"""
#     # In production, this would integrate with Stripe/RevenueCat
#     # For now, we'll simulate the upgrade
#     pro_expires_at = datetime.utcnow() + timedelta(days=30)  # 30-day subscription
    
#     db = get_db()
#     await firebase_db.update_user(
#         current_user["id"],
#         {
#             "is_pro": True,
#             "plan_type": "pro",
#             "pro_expires_at": pro_expires_at,
#         }
#     )
    
#     return {
#         "message": "Successfully upgraded to Pro!",
#         "plan_type": "pro",
#         "pro_expires_at": pro_expires_at
#     }

@api_router.post("/users/cancel-pro")
async def cancel_pro(current_user: dict = Depends(get_current_user)):
    """Cancel Pro subscription"""
    db = get_db()
    await firebase_db.update_user(
        current_user["id"],
        {
            "is_pro": False,
            "plan_type": "free",
            "pro_expires_at": None
        }
    )
    
    return {"message": "Pro subscription cancelled", "plan_type": "free"}

# ============== Advanced Search (Pro Feature) ==============

@api_router.get("/search/advanced")
async def advanced_search(
    q: str,
    search_notes: bool = True,
    search_tags: bool = True,
    search_titles: bool = True,
    platform: Optional[str] = None,
    collection_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    # ✅ Pro check from Firebase user
    is_pro = (
        current_user.get("is_pro", False)
        or current_user.get("plan_type") == "pro"
    )

    if not is_pro:
        raise HTTPException(
            status_code=403,
            detail="Advanced search is a Pro feature. Upgrade to unlock."
        )

    db = get_db()

    search_conditions = []

    if search_titles:
        search_conditions.append({"title": {"$regex": q, "$options": "i"}})
    if search_notes:
        search_conditions.append({"notes": {"$regex": q, "$options": "i"}})
    if search_tags:
        search_conditions.append({"tags": {"$regex": q, "$options": "i"}})

    query = {
        "user_id": current_user["id"],
        "$or": search_conditions
    }

    if platform:
        query["platform"] = platform
    if collection_id:
        query["collections"] = collection_id

    items = await db.items.find(query).sort("created_at", -1).to_list(100)

    return {
        "results": [SavedItemResponse(**item) for item in items],
        "total": len(items),
        "search_in": {
            "titles": search_titles,
            "notes": search_notes,
            "tags": search_tags
        }
    }

# ============== Vault Export (Pro Feature) ==============

@api_router.get("/export/vault")
async def export_vault(current_user: dict = Depends(get_current_user)):
    is_pro = (
        current_user.get("is_pro", False)
        or current_user.get("plan_type") == "pro"
    )

    if not is_pro:
        raise HTTPException(
            status_code=403,
            detail="Vault export is a Pro feature. Upgrade to unlock."
        )

    db = get_db()

    items = await db.items.find(
        {"user_id": current_user["id"]}
    ).to_list(1000)

    collections = await db.collections.find(
        {"user_id": current_user["id"]}
    ).to_list(100)

    return {
        "exported_at": datetime.utcnow().isoformat(),
        "user": {
            "email": current_user.get("email"),
            "name": current_user.get("name"),
            "created_at": current_user.get("created_at").isoformat()
            if current_user.get("created_at") else None,
        },
        "statistics": {
            "total_items": len(items),
            "total_collections": len(collections),
        },
        "collections": [
            {
                "id": c["id"],
                "name": c["name"],
                "created_at": c["created_at"].isoformat()
                if c.get("created_at") else None,
            }
            for c in collections
        ],
        "items": [
            {
                "id": item["id"],
                "url": item.get("url"),
                "title": item.get("title"),
                "platform": item.get("platform"),
                "notes": item.get("notes"),
                "tags": item.get("tags", []),
                "collections": item.get("collections", []),
                "created_at": item["created_at"].isoformat()
                if item.get("created_at") else None,
                "ai_summary": item.get("ai_summary"),
                "action_items": item.get("action_items"),
            }
            for item in items
        ]
    }


# ============== Smart Reminders (Pro Feature) ==============

@api_router.get("/reminders")
async def get_smart_reminders(current_user: dict = Depends(get_current_user)):
    is_pro = (
        current_user.get("is_pro", False)
        or current_user.get("plan_type") == "pro"
    )

    if not is_pro:
        raise HTTPException(
            status_code=403,
            detail="Smart reminders is a Pro feature. Upgrade to unlock."
        )

    db = get_db()
    reminders = []
    
    # Items saved 7 days ago (weekly review)
    week_ago = datetime.utcnow() - timedelta(days=7)
    week_ago_start = week_ago.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago_end = week_ago.replace(hour=23, minute=59, second=59, microsecond=999999)

    
    
    weekly_items = await db.items.find({
        "user_id": current_user["id"],
        "created_at": {"$gte": week_ago_start, "$lte": week_ago_end}
    }).to_list(5)
    
    if weekly_items:
        reminders.append({
            "type": "weekly_review",
            "title": "Weekly Review",
            "message": f"You saved {len(weekly_items)} items exactly a week ago. Time to review!",
            "items": [{"id": i["id"], "title": i.get("title", "Untitled")} for i in weekly_items],
            "priority": "medium"
        })
    
    # Items with incomplete action items
    
    items_with_actions = await db.items.find({
        "user_id": current_user["id"],
        "action_items": {"$exists": True, "$ne": []},
    }).to_list(100)
    
    incomplete_actions = []
    for item in items_with_actions:
        actions = item.get("action_items", [])
        incomplete = [a for a in actions if not a.get("completed", False)]
        if incomplete:
            incomplete_actions.append({
                "item_id": item["id"],
                "item_title": item.get("title", "Untitled"),
                "pending_tasks": len(incomplete)
            })
    
    if incomplete_actions:
        reminders.append({
            "type": "pending_actions",
            "title": "Pending Action Items",
            "message": f"You have pending tasks on {len(incomplete_actions)} saved items.",
            "items": incomplete_actions[:5],
            "priority": "high"
        })
    
    # Items saved 30+ days ago (resurface)
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    old_items = await db.items.find({
        "user_id": current_user["id"],
        "created_at": {"$lte": thirty_days_ago}
    }).sort("created_at", 1).limit(5).to_list(5)
    
    if old_items:
        reminders.append({
            "type": "resurface",
            "title": "Forgotten Gems",
            "message": "These items from your vault might be worth revisiting.",
            "items": [
                {
                    "id": i["id"], 
                    "title": i.get("title", "Untitled"),
                    "days_ago": (datetime.utcnow() - i["created_at"]).days
                } 
                for i in old_items
            ],
            "priority": "low"
        })
    
    return {"reminders": reminders, "total": len(reminders)}

@api_router.post("/items/{item_id}/ai-summary")
async def generate_item_summary(
    item_id: str,
    bg: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    require_pro(current_user)
    bg.add_task(run_ai_summary_job, item_id, current_user["id"])
    return {"status": "processing"}


@api_router.post("/payments/create-checkout-session")
async def create_checkout_session(
    data: CheckoutSessionRequest,
    current_user: dict = Depends(get_current_user),
):
    # 1️⃣ Validate plan
    if data.plan not in ["monthly", "yearly"]:
        raise HTTPException(status_code=400, detail="Invalid plan")

    price_id = (
        MONTHLY_PRICE_ID
        if data.plan == "monthly"
        else YEARLY_PRICE_ID
    )
    logger.info(f"Checkout plan={data.plan}, price_id={price_id}")
    if not price_id:
        raise HTTPException(
            status_code=500,
            detail="Stripe price ID not configured"
        )

    # 2️⃣ Find or create Stripe customer
    customers = stripe.Customer.list(
        email=current_user["email"],
        limit=1
    )

    if customers.data:
        customer = customers.data[0]
    else:
        customer = stripe.Customer.create(
            email=current_user["email"],
            metadata={
                "user_id": current_user["id"]
            }
        )

    if current_user.get("is_pro"):
        raise HTTPException(
            status_code=400,
            detail="User already has an active subscription"
        )

    # 3️⃣ Create Checkout Session
    session = stripe.checkout.Session.create(
        customer=customer.id,
        mode="subscription",
        payment_method_types=["card"],
        line_items=[
            {
                "price": price_id,
                "quantity": 1
            }
        ],
        success_url=os.getenv("STRIPE_SUCCESS_URL"),
        cancel_url=os.getenv("STRIPE_CANCEL_URL"),
        metadata={
            "user_id": current_user["id"],
            "plan": data.plan
        },
        subscription_data={
            "metadata": {
                "user_id": current_user["id"],
                "plan": data.plan
            }
        }
    )

    # 4️⃣ Return checkout URL
    return {
        "checkout_url": session.url
    }


async def is_event_processed(event_id: str) -> bool:
    db = get_db()
    existing = await db.webhook_events.find_one({"id": event_id})
    return existing is not None


async def mark_event_processed(event: dict):
    db = get_db()
    await db.webhook_events.insert_one({
        "id": event["id"],
        "type": event["type"],
        "created_at": datetime.utcnow(),
    })


@api_router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=endpoint_secret
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook error")

    # 🔐 IDEMPOTENCY CHECK
    if await is_event_processed(event["id"]):
        logger.info(f"Duplicate webhook ignored: {event['id']}")
        return {"status": "duplicate"}

    event_type = event["type"]
    data = event["data"]["object"]

    try:
        if event_type == "checkout.session.completed":
            await handle_checkout_completed(data)
        elif event_type == "invoice.payment_succeeded":
            await handle_invoice_paid(data)
        elif event_type == "invoice.payment_failed":
            await handle_invoice_failed(data)
        elif event_type == "customer.subscription.deleted":
            await handle_subscription_canceled(data)
        elif event_type == "customer.subscription.updated":
            await handle_subscription_updated(data)

            # ✅ Mark event processed ONLY after success
            await mark_event_processed(event)
    except Exception as e:
        logger.error(f"Webhook handler error ({event_type}): {e}")

        

    except Exception as e:
        logger.error(f"Webhook processing failed: {e}")
        raise HTTPException(status_code=500, detail="Webhook handler failed")

    return {"status": "success"}


async def handle_checkout_completed(session):
    db = get_db()

    user_id = session["metadata"]["user_id"]
    plan = session["metadata"]["plan"]

    logger.info(f"Updating user {user_id} to PRO")

    subscription_id = session["subscription"]
    customer_id = session["customer"]

    subscription = stripe.Subscription.retrieve(subscription_id)

    existing = await db.subscriptions.find_one({
        "stripe_subscription_id": subscription.id
    })
    if existing:
        return

    # ✅ SAFE ACCESS (this is the fix)
    current_period_start = subscription.get("current_period_start")
    current_period_end = subscription.get("current_period_end")

    price_id = None
    if subscription.get("items") and subscription["items"]["data"]:
        price_id = subscription["items"]["data"][0]["price"]["id"]

    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,

        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription.id,
        "stripe_price_id": price_id,

        "plan": plan,
        "status": subscription.status,

        "current_period_start": (
            datetime.utcfromtimestamp(current_period_start)
            if current_period_start else None
        ),
        "current_period_end": (
            datetime.utcfromtimestamp(current_period_end)
            if current_period_end else None
        ),

        "cancel_at_period_end": subscription.cancel_at_period_end,

        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    await db.subscriptions.insert_one(doc)

    # ✅ USER UPDATE WILL NOW ALWAYS RUN
    await firebase_db.update_user(user_id, {
        "is_pro": True,
        "plan_type": "pro",
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription.id,
        "pro_expires_at": doc["current_period_end"],
    })

    updated_user = await firebase_db.get_user_by_id(user_id)
    logger.info(f"User after update: {updated_user}")

async def handle_invoice_paid(invoice):
    db = get_db()

    subscription_id = invoice.get("subscription")
    if not subscription_id:
        logger.warning("Invoice without subscription, skipping")
        return

    subscription = stripe.Subscription.retrieve(subscription_id)

    # Update local subscription record
    await db.subscriptions.update_one(
        {"stripe_subscription_id": subscription.id},
        {"$set": {
            "status": subscription.status,
            "current_period_end": datetime.utcfromtimestamp(
                subscription.current_period_end
            ),
            "updated_at": datetime.utcnow()
        }}
    )

    # Update user
    customer_id = subscription["customer"]
    user = await firebase_db.get_user_by_stripe_customer_id(customer_id)

    if user:
        await firebase_db.update_user(user["id"], {
            "is_pro": True,
            "plan_type": "pro",
            "pro_expires_at": datetime.utcfromtimestamp(
                subscription.current_period_end
            )
        })


async def handle_invoice_failed(invoice):
    db = get_db()

    subscription_id = invoice.get("subscription")

    if not subscription_id:
        logger.warning("Invoice without subscription, skipping")
        return

    await db.subscriptions.update_one(
        {"stripe_subscription_id": invoice["subscription"]},
        {"$set": {
            "status": "past_due",
            "updated_at": datetime.utcnow()
        }}
    )

    subscription_id = invoice["subscription"]
    subscription = stripe.Subscription.retrieve(subscription_id)

    customer_id = subscription["customer"]
    user = await firebase_db.get_user_by_stripe_customer_id(customer_id)

    if user:
        await firebase_db.update_user(user["id"], {
            "is_pro": False,
            "plan_type": "free"
        })


async def handle_subscription_canceled(subscription):
    db = get_db()

    await db.subscriptions.update_one(
        {"stripe_subscription_id": subscription["id"]},
        {"$set": {
            "status": "canceled",
            "updated_at": datetime.utcnow()
        }}
    )

    customer_id = subscription["customer"]

    user = await firebase_db.get_user_by_stripe_customer_id(customer_id)

    if user:
        await firebase_db.update_user(user["id"], {
            "is_pro": False,
            "plan_type": "free",
            "pro_expires_at": None
        })

async def handle_subscription_updated(subscription):
    if not subscription or "id" not in subscription:
        logger.warning("Invalid subscription update payload")
        return

    db = get_db()

    await db.subscriptions.update_one(
        {"stripe_subscription_id": subscription["id"]},
        {"$set": {
            "status": subscription["status"],
            "cancel_at_period_end": subscription.get("cancel_at_period_end"),
            "current_period_end": datetime.utcfromtimestamp(
                subscription["current_period_end"]
            ),
            "updated_at": datetime.utcnow()
        }}
    )

    customer_id = subscription.get("customer")
    if not customer_id:
        return

    user = await firebase_db.get_user_by_stripe_customer_id(customer_id)
    if not user:
        return

    await firebase_db.update_user(user["id"], {
        "is_pro": subscription["status"] == "active",
        "plan_type": "pro" if subscription["status"] == "active" else "free",
        "pro_expires_at": datetime.utcfromtimestamp(
            subscription["current_period_end"]
        )
    })

# ============== Health Check ==============

@api_router.get("/")
async def root():
    return {"message": "Stash API is running", "version": "1.0.0"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy"}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    if client:
        client.close()
