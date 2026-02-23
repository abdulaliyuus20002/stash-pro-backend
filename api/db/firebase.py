import firebase_admin
from firebase_admin import credentials, firestore
import os

# Prevent re-initialization (important for FastAPI reloads)
if not firebase_admin._apps:
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    if not cred_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set")

    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

db = firestore.client()


class FirebaseDB:
    # 🔍 READ
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

    # ✨ CREATE
    async def create_user(self, user: dict):
        db.collection("users").document(user["id"]).set(user)

    # ✏️ UPDATE  ← 🔥 ADD YOUR METHODS HERE
    async def update_user(self, user_id: str, data: dict):
        db.collection("users").document(user_id).update(data)

    # ❌ DELETE  ← 🔥 ADD YOUR METHODS HERE
    async def delete_user(self, user_id: str):
        db.collection("users").document(user_id).delete()