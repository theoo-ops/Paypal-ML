from google import genai
import os
from dotenv import load_dotenv

load_dotenv()

try:
    client = genai.Client()
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents="Say hello and confirm you are working!",
    )
    print("Success! Response from Gemini:")
    print(response.text)
except Exception as e:
    print("Error connecting to Gemini API:", e)