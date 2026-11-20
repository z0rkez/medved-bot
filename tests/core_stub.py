"""Локальная заглушка core для проверки входа из браузера."""

from fastapi import FastAPI, Header, HTTPException

app = FastAPI()


@app.get("/api/v3/users/user-for-external-service")
def patient(token: str, authorization: str = Header()):
    if (token != "test" and not token.startswith("test-")) or authorization != f"Bearer {token}":
        raise HTTPException(401)
    return {"birthDate": "1987-10-26", "full_name": {"firstName": "Анна", "middleName": "Петровна"}}
