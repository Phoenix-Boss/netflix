import WebTorrent from 'webtorrent';
import express from 'express';
import cors from 'cors';

const app = express();
app.use(cors());

const client = new WebTorrent({ maxConns: 50, tracker: false });
const activeTorrents = new Map();

const TRACKERS = [
    'wss://tracker.openwebtorrent.com',
    'wss://tracker.btorrent.xyz',
    'wss://tracker.fastcast.nz'
];

app.get('/health', (req, res) => {
    res.json({ status: 'healthy', torrents: activeTorrents.size });
});

app.get('/stream', (req, res) => {
    const magnet = req.query.magnet;
    if (!magnet) return res.status(400).send('Missing magnet');

    if (activeTorrents.has(magnet)) {
        const torrent = activeTorrents.get(magnet);
        const file = getLargestVideo(torrent);
        if (file) return pipeFile(file, res);
    }

    console.log('[NEW] Adding torrent...');
    
    client.add(magnet, { announce: TRACKERS }, (torrent) => {
        activeTorrents.set(magnet, torrent);

        torrent.on('ready', () => {
            const file = getLargestVideo(torrent);
            if (!file) {
                res.status(404).send('No video file found');
                return cleanup(magnet, torrent);
            }
            console.log('[READY] Streaming: ' + file.name);
            pipeFile(file, res);
        });

        setTimeout(() => {
            if (!torrent.files || torrent.files.length === 0) {
                console.log('[TIMEOUT] No peers found');
                if (!res.headersSent) res.status(504).send('Torrent timed out');
                cleanup(magnet, torrent);
            }
        }, 20000);
    });

    req.on('close', () => {
        const torrent = activeTorrents.get(magnet);
        if (torrent) cleanup(magnet, torrent);
    });
});

function getLargestVideo(torrent) {
    let largest = null, maxSize = 0;
    for (const file of torrent.files) {
        const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
        if (['.mp4', '.mkv', '.webm'].includes(ext) && file.length > maxSize) {
            largest = file; maxSize = file.length;
        }
    }
    return largest;
}

function pipeFile(file, res) {
    res.setHeader('Content-Type', 'video/mp4');
    res.setHeader('Content-Disposition', 'inline; filename="' + file.name + '"');
    file.createReadStream().pipe(res);
}

function cleanup(magnet, torrent) {
    try {
        torrent.destroy();
        activeTorrents.delete(magnet);
        console.log('[CLEANUP] RAM freed.');
    } catch (e) {}
}

const PORT = process.env.PORT || 3000;
app.listen(PORT, '0.0.0.0', () => console.log('Streamer running on port ' + PORT));