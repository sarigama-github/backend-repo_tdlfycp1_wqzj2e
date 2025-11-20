import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


class VerifyBody(BaseModel):
    key: str


@app.get("/")
def read_root():
    return {"message": "GauravBuilds Backend Running"}


@app.post("/api/auth/verify")
def verify_owner(body: VerifyBody):
    owner_key = os.getenv("OWNER_KEY", "changeme")
    if body.key and body.key == owner_key:
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Invalid key")


@app.get("/api/plugins")
def list_plugins(limit: Optional[int] = None):
    try:
        items = get_documents("plugin", {}, limit)
        # Normalize ids and strip filename from response
        normalized = []
        for it in items:
            normalized.append({
                "id": str(it.get("_id")),
                "name": it.get("name"),
                "description": it.get("description"),
                "version": it.get("version"),
                "original_name": it.get("original_name"),
                "file_size": it.get("file_size", 0),
                "download_count": it.get("download_count", 0),
                "created_at": it.get("created_at")
            })
        return {"plugins": normalized}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.post("/api/plugins/upload")
async def upload_plugin(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    version: Optional[str] = Form(None),
    file: UploadFile = File(...),
    x_owner_key: Optional[str] = Header(None)
):
    owner_key = os.getenv("OWNER_KEY", "changeme")
    if x_owner_key != owner_key:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Validate extension
    original_name = file.filename or "plugin.jar"
    if not original_name.lower().endswith(".jar"):
        raise HTTPException(status_code=400, detail="Only .jar files are allowed")

    # Create safe filename
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    safe_base = os.path.basename(original_name).replace(" ", "_")
    stored_name = f"{timestamp}_{safe_base}"
    stored_path = os.path.join(UPLOAD_DIR, stored_name)

    # Save the uploaded file
    contents = await file.read()
    with open(stored_path, "wb") as f:
        f.write(contents)

    size = len(contents)

    # Insert metadata
    doc = {
        "name": name,
        "description": description,
        "version": version,
        "filename": stored_name,
        "original_name": original_name,
        "file_size": size,
        "download_count": 0
    }
    try:
        new_id = create_document("plugin", doc)
        return {"ok": True, "id": new_id}
    except Exception as e:
        # Roll back file if db fails
        try:
            os.remove(stored_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/plugins/{plugin_id}/download")
def download_plugin(plugin_id: str):
    try:
        it = db["plugin"].find_one({"_id": ObjectId(plugin_id)})
        if not it:
            raise HTTPException(status_code=404, detail="Not found")
        stored_name = it.get("filename")
        original_name = it.get("original_name") or stored_name
        file_path = os.path.join(UPLOAD_DIR, stored_name)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File missing on server")
        # Increment download counter
        db["plugin"].update_one({"_id": ObjectId(plugin_id)}, {"$inc": {"download_count": 1}})
        return FileResponse(path=file_path, media_type="application/java-archive", filename=original_name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
