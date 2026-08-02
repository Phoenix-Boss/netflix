const { chromium } = require('playwright');

async function robustGoto(page, url, retries) {
    retries = retries || 3;
    for (var i = 0; i < retries; i++) {
        try {
            await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
            return true;
        } catch (e) {
            if (i === retries - 1) throw e;
            await page.waitForTimeout(3000);
        }
    }
}

function getSimilarity(a, b) {
    a = a.toLowerCase().replace(/[^a-z0-9]/g, '');
    b = b.toLowerCase().replace(/[^a-z0-9]/g, '');
    if (a === b) return 100;
    if (a.length > 0 && b.length > 0 && (a.indexOf(b) !== -1 || b.indexOf(a) !== -1)) return 100;
    if (a.length === 0 || b.length === 0) return 0;
    var matrix = [];
    for (var i = 0; i <= b.length; i++) matrix[i] = [i];
    for (var j = 0; j <= a.length; j++) matrix[0][j] = j;
    for (var i = 1; i <= b.length; i++) {
        for (var j = 1; j <= a.length; j++) {
            if (b.charAt(i - 1) === a.charAt(j - 1)) matrix[i][j] = matrix[i - 1][j - 1];
            else matrix[i][j] = Math.min(matrix[i - 1][j - 1] + 1, matrix[i][j - 1] + 1, matrix[i - 1][j] + 1);
        }
    }
    var maxLen = Math.max(a.length, b.length);
    return ((maxLen - matrix[b.length][a.length]) / maxLen) * 100;
}

(async () => {
    var movieName = process.argv[2];
    if (!movieName) { process.exit(1); }

    var baseUrl = "https://fzmovies.live";
    var browser = await chromium.launch({ headless: true, args: ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-sandbox"] });
    var context = await browser.newContext({ userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", viewport: { width: 1920, height: 1080 }, locale: 'en-US' });
    await context.addInitScript("Object.defineProperty(navigator, 'webdriver', { get: function() { return undefined; } }); window.open = function() { return null; };");
    var page = await context.newPage();

    try {
        await robustGoto(page, baseUrl + "/csearch.php");
    } catch(e) { await browser.close(); process.exit(1); }
    
    await page.fill("#searchname", movieName);
    await page.click("input[type='submit']");
    await page.waitForLoadState("domcontentloaded");
    
    var searchResults = await page.evaluate(function() {
        var results = []; var html = document.body.innerHTML;
        var regex = /href=\"(movie-[^"]+\.htm)\"[^>]*>([\s\S]*?)<\/a>/gi; var match;
        while ((match = regex.exec(html)) !== null) {
            var rawText = match[2].replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim();
            if (rawText.length > 3) { var ym = rawText.match(/\((\d{4})\)/); results.push({ url: match[1], title: rawText, year: ym ? parseInt(ym[1]) : 0 }); }
        }
        results.sort(function(a, b) { return b.year - a.year; });
        return results;
    });
    
    var selectedMovie = null;
    for (var i = 0; i < searchResults.length; i++) {
        if (getSimilarity(movieName, searchResults[i].title) >= 90) { selectedMovie = searchResults[i]; break; }
    }
    if (!selectedMovie) { await browser.close(); process.exit(1); }

    await robustGoto(page, baseUrl + "/" + selectedMovie.url.replace(/^\//, ""));
    try { await page.waitForSelector("a[href*='download1.php'], a[onclick*='download1.php']", { timeout: 15000 }); } catch(e) { await browser.close(); process.exit(1); }
    
    var qualities = await page.evaluate(function(baseUrl) {
        var results = [], links = document.querySelectorAll("a"), seen = {};
        for (var i = 0; i < links.length; i++) {
            var el = links[i], text = (el.innerText || "").trim();
            if (text.indexOf(".mp4") === -1 && text.indexOf(".mkv") === -1) continue;
            var url = "", href = el.getAttribute("href") || "", onclick = el.getAttribute("onclick") || "";
            if (href.indexOf("download1.php") !== -1) url = href.indexOf("http") === 0 ? href : baseUrl + "/" + href.replace(/^\//, "");
            else if (onclick.indexOf("download1.php") !== -1) { var m = onclick.match(/download1\.php\?downloadoptionskey=([^&\"']+)&pt=([^\"']+)/); if(m) url = baseUrl + "/download1.php?downloadoptionskey=" + m[1] + "&pt=" + m[2]; }
            else { var p = el.closest("li") || el.parentElement; if(p) { var pl = p.querySelectorAll("a[href*='download1.php'], a[onclick*='download1.php']"); for(var j=0;j<pl.length;j++) { var ph=pl[j].getAttribute("href")||"", po=pl[j].getAttribute("onclick")||""; if(ph.indexOf("download1.php")!==-1){url=ph.indexOf("http")===0?ph:baseUrl+"/"+ph.replace(/^\//,"");break;} else if(po.indexOf("download1.php")!==-1){var pm=po.match(/download1\.php\?downloadoptionskey=([^&\"']+)&pt=([^\"']+)/);if(pm){url=baseUrl+"/download1.php?downloadoptionskey="+pm[1]+"&pt="+pm[2];break;}}}}}
            if (!url || url.indexOf("download1.php") === -1 || seen[url]) continue; seen[url] = true;
            var pt = (el.closest("li") || el.parentElement).innerText || ""; var size = "Unknown", sm = pt.match(/\(\s*(\d+(?:\.\d+)?)\s*(MB|GB)\s*\)/i); if(sm) size = sm[1] + " " + sm[2].toUpperCase();
            results.push({ url: url, text: text.replace(/\s+/g, " ").trim(), size: size });
        }
        return results;
    }, baseUrl);

    var finalResults = [];
    for (var i = 0; i < qualities.length; i++) {
        var quality = qualities[i];
        if (quality.url.indexOf("http") !== 0) continue;
        try {
            await robustGoto(page, quality.url);
            var clicked = false;
            var btns = ["text=DOWNLOAD THIS MOVIE ON YOUR DEVICE", "a:has-text('DOWNLOAD')", "input[type='submit']"];
            for (var s = 0; s < btns.length; s++) { try { var b = await page.waitForSelector(btns[s], { timeout: 5000 }); if(b){await b.click(); clicked=true;break;}}catch(e){continue;} }
            if (!clicked) continue;
            await page.waitForURL("**/download.php**", { timeout: 20000 }); await page.waitForTimeout(2000);
            var mp4Links = await page.evaluate(function(baseUrl) {
                var links = [], regex = /href=\"(dlink\.php\?[^"]+)\"/gi, match;
                while ((match = regex.exec(document.body.innerHTML)) !== null) { var link = match[1].replace(/&amp;/g, "&"); links.push(link.indexOf("http") === 0 ? link : baseUrl + "/" + link.replace(/^\//, "")); }
                return links;
            }, baseUrl);
            if (mp4Links.length > 0) finalResults.push({ file: quality.text, size: quality.size, downloads: mp4Links });
        } catch (e) { continue; }
    }
    await browser.close();
    console.log(JSON.stringify(finalResults));
})();
