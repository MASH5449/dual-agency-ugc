import os
from fastapi import FastAPI, Request

app = FastAPI(title="Dual Agency UGC Engine")

@app.get("/")
def health_check():
    return {"status": "online", "agency": "Dual UGC Autonomous Infrastructure"}

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    return {"status": "success", "message": "Event received"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
