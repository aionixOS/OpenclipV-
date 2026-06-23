from fastapi import Request, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://Aionixos:Allowed@botdb.ppc3ixq.mongodb.net/Openclip?retryWrites=true&w=majority")
client = AsyncIOMotorClient(MONGODB_URI)
# Note: Ensure the DB name matches what NextAuth creates
db = client.get_database("Openclip")

async def get_current_user(request: Request) -> str:
    # 1. Try to get token from cookies
    session_token = request.cookies.get("next-auth.session-token") or request.cookies.get("__Secure-next-auth.session-token")
    
    # 2. Try Authorization header
    if not session_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            session_token = auth_header.split(" ")[1]
            
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    # Query MongoDB
    session = await db.sessions.find_one({"sessionToken": session_token})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
        
    # Check expiry
    expires = session.get("expires")
    if expires and expires.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")
        
    user = await db.users.find_one({"_id": session["userId"]})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
        
    return str(user["_id"])
