const WebTorrent = require("webtorrent");
const express = require("express");
const cors = require("cors");
const app = express();
app.use(cors());
const client = new WebTorrent({ maxConns: 50, tracker: false });
const activeTorrents = new Map();
const TRACKERS = ["wss://tracker.openwebtorrent.com","wss://tracker.btorrent.xyz","wss://tracker.fastcast.nz"];

app.get("/health", (req, res) => res.json({ status: "healthy" }));

app.get("/stream", (req, res) => {
    const magnet = req.query.magnet;
    if (!magnet) return res.status(400).send("Missing magnet");
    if (activeTorrents.has(magnet)) {
        const file = getLargestVideo(activeTorrents.get(magnet));
        if (file) return pipeFile(file, res);
    }
    console.log("[NEW] Adding torrent...");
    client.add(magnet, { announce: TRACKERS }, (torrent) => {
        activeTorrents.set(magnet, torrent);
        torrent.on("ready", () => {
            const file = getLargestVideo(torrent);
            if (!file) { res.status(404).send("No video"); return cleanup(magnet, torrent); }
            console.log("[READY] " + file.name);
            pipeFile(file, res);
        });
        setTimeout(() => {
            if (!torrent.files || torrent.files.length === 0) {
                if (!res.headersSent) res.status(504).send("Timeout");
                cleanup(magnet, torrent);
            }
        }, 20000);
    });
    req.on("close", () => { const t = activeTorrents.get(magnet); if(t) cleanup(magnet, t); });
});

function getLargestVideo(torrent) {
    let largest = null, maxSize = 0;
    for (const file of torrent.files) {
        const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
        if ([".mp4", ".mkv", ".webm"].includes(ext) && file.length > maxSize) { largest = file; maxSize = file.length; }
    }
    return largest;
}

function pipeFile(file, res) {
    res.setHeader("Content-Type", "video/mp4");
    file.createReadStream().pipe(res);
}

function cleanup(magnet, torrent) {
    try { torrent.destroy(); activeTorrents.delete(magnet); console.log("[CLEANUP] RAM freed."); } catch(e) {}
}

app.listen(process.env.PORT || 3000, () => console.log("Streamer running on port " + (process.env.PORT || 3000)));
