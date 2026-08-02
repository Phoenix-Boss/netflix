import sys
import ddddocr

# Initialize the OCR engine (show_ad=False removes the startup banner)
ocr = ddddocr.DdddOcr(show_ad=False)

# Get image path from Node.js argument
img_path = sys.argv[1]

with open(img_path, 'rb') as f:
    img_bytes = f.read()

# Solve and print ONLY the result so Node.js can read it
result = ocr.classification(img_bytes)
print(result)