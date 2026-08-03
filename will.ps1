# Create the test file
@"
import requests
import json
from datetime import datetime

BASE_URL = "https://netflix-tf79.onrender.com"

def print_test(test_name, status, details=""):
    emoji = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⚠️"
    print(f"{emoji} {test_name}: {status}")
    if details:
        print(f"   └─ {details}")

def test_endpoint(url, expected_status=200, timeout=15):
    try:
        response = requests.get(url, timeout=timeout)
        status = "PASS" if response.status_code == expected_status else "FAIL"
        details = f"Status Code: {response.status_code}"
        try:
            data = response.json()
            details += f" | Response: {json.dumps(data, indent=2)[:200]}..."
        except:
            details += f" | Response: {response.text[:100]}..."
        return status, details
    except requests.exceptions.RequestException as e:
        return "FAIL", f"Request Error: {str(e)}"

print("=" * 60)
print(f"🚀 STARTING API TESTS at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"🌐 Target: {BASE_URL}")
print("=" * 60)

# Test endpoints
print("\n📡 [1] HEALTH CHECK")
status, details = test_endpoint(f"{BASE_URL}/")
print_test("Root Endpoint", status, details)

print("\n🎬 [2] PRIMARY STREAM (Movie)")
status, details = test_endpoint(f"{BASE_URL}/stream/tt0111161")
print_test("GET /stream/tt0111161", status, details)

print("\n📺 [3] PRIMARY STREAM (TV Show)")
status, details = test_endpoint(f"{BASE_URL}/stream/tt0903743?s=1&e=1")
print_test("GET /stream/tt0903743?s=1&e=1", status, details)

print("\n📱 [4] SMART TV ENDPOINT")
status, details = test_endpoint(f"{BASE_URL}/smart/tt0903743?s=1&e=1&q=480p")
print_test("GET /smart/tt0903743?s=1&e=1&q=480p", status, details)

print("\n🔄 [5] FALLBACK ENDPOINT")
status, details = test_endpoint(f"{BASE_URL}/fallback/Fight+Club?q=1080p")
print_test("GET /fallback/Fight+Club?q=1080p", status, details)

print("\n🚫 [6] ERROR HANDLING (Invalid ID)")
status, details = test_endpoint(f"{BASE_URL}/stream/invalid_id", expected_status=404)
print_test("GET /stream/invalid_id (Expected 404)", status, details)

print("\n" + "=" * 60)
print("🏁 TESTING COMPLETE")
print("=" * 60)
"@ | Out-File -FilePath test_render_api.py -Encoding UTF8

# Now run the test
python test_render_api.py