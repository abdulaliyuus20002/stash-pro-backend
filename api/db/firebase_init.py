import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

def init_firebase():
    if firebase_admin._apps:
        return firestore.client()

    # OPTION A: JSON file path
    key_path = os.getenv("FIREBASE_KEY_PATH")

    # OPTION B: JSON string
    key_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")

    if key_path:
        cred = credentials.Certificate(key_path)
    elif key_json:
        cred = credentials.Certificate(json.loads(key_json))
    else:
        raise RuntimeError("Firebase credentials not configured")

    firebase_admin.initialize_app(cred)
    return firestore.client()