import os
import sys

# Add parent dir to path so we can import app
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings
from qdrant_client import QdrantClient
from qdrant_client.http import models

def init_qdrant():
    print(f"Connecting to Qdrant at {settings.QDRANT_URL}...")
    client = QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
    )
    
    collection_name = settings.QDRANT_COLLECTION
    print(f"Checking collection '{collection_name}'...")
    
    # 1. Ensure Collection
    try:
        info = client.get_collection(collection_name)
        if info.config.params.vectors.size != 384:
            print(f"Dimension mismatch! Expected 384, got {info.config.params.vectors.size}. Recreating...")
            client.delete_collection(collection_name)
            raise Exception("Recreate")
        print("Collection exists and is valid.")
    except Exception as e:
        print(f"Creating collection '{collection_name}'...")
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
        )
        print("Collection created.")

    # 2. Ensure Index
    print("Creating 'tenant_id' index...")
    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name="tenant_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        print("Index 'tenant_id' created successfully.")
    except Exception as e:
        print(f"Index creation note (might exist): {e}")

if __name__ == "__main__":
    init_qdrant()
