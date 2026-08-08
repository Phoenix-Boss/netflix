cd C:\Users\Boss\Desktop\EDGES\vidsrc-api
Remove-Item "streamer\Dockerfile" -Force
Remove-Item "streamer\.dockerignore" -Force

git add -A
git commit -m "Remove Dockerfile from streamer (using native Render Node)"
git push origin main