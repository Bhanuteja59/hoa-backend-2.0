import requests

with open("test_upload.txt", "w") as f:
    f.write("test content")

with open("test_upload.txt", "rb") as f:
    files = {"file": ("test_upload.txt", f, "text/plain")}
    response = requests.post("http://localhost:8000/api/v1/uploads", files=files)

print("Status Code:", response.status_code)
print("Response Body:", response.text)
