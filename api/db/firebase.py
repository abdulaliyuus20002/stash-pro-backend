import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

if not firebase_admin._apps:
    # LOCAL: file path
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    # PROD (Vercel): JSON string
    cred_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")

    if cred_path:
        cred = credentials.Certificate(cred_path)
    elif cred_json:
        cred = credentials.Certificate(json.loads(cred_json))
    else:
        raise RuntimeError("Firebase credentials not configured")

    firebase_admin.initialize_app(cred)

db = firestore.client()


class FirebaseDB:
    async def get_user_by_email(self, email: str):
        docs = (
            db.collection("users")
            .where("email", "==", email.lower())
            .limit(1)
            .stream()
        )
        for doc in docs:
            return doc.to_dict()
        return None

    async def get_user_by_id(self, user_id: str):
        doc = db.collection("users").document(user_id).get()
        return doc.to_dict() if doc.exists else None

    async def create_user(self, user: dict):
        db.collection("users").document(user["id"]).set(user)

    async def update_user(self, user_id: str, data: dict):
        db.collection("users").document(user_id).update(data)

    async def delete_user(self, user_id: str):
        db.collection("users").document(user_id).delete()