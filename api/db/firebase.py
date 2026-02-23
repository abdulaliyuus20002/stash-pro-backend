import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

if not firebase_admin._apps:
    firebase_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

    if not firebase_json:
        raise RuntimeError(
            "FIREBASE_SERVICE_ACCOUNT_JSON is not set"
        )

    # 🔥 IMPORTANT: json.loads → dict
    cred = credentials.Certificate(json.loads(firebase_json))
    firebase_admin.initialize_app(cred)

db = firestore.client()


class FirebaseDB:
    async def get_user_by_email(self, email: str):
        users = (
            db.collection("users")
            .where("email", "==", email.lower())
            .limit(1)
            .stream()
        )
        for user in users:
            return user.to_dict()
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