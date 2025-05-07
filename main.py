#!/usr/bin/env python3
# FastAPI service to extract m3u8 links from eval-packed JavaScript
import re
import logging
import execjs
import requests
from typing import List, Optional
from urllib.parse import urlparse, parse_qs
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from fastapi.middleware.cors import CORSMiddleware

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="M3U8 Scraper API",
    description="API to scrape m3u8 links from eval-packed JavaScript",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Response models
class M3U8Response(BaseModel):
    success: bool
    slug: Optional[str] = None
    total_packed_scripts: Optional[int] = None
    m3u8_links: List[str] = []
    count: int = 0
    error: Optional[str] = None


class VideoScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://animedub.pro/'
        })

    def extract_slug_from_url(self, url):
        """Extract the slug/ID from the URL."""
        parsed_url = urlparse(url)
        path_parts = parsed_url.path.strip('/').split('/')
        
        # The last part of the path is typically the slug
        if path_parts:
            return path_parts[-1]
        
        # Try to get from query parameters
        query_params = parse_qs(parsed_url.query)
        if 'id' in query_params:
            return query_params['id'][0]
            
        return None

    def find_eval_packed_js(self, html_content):
        """Find eval-packed JavaScript in HTML content."""
        pattern = r'eval\(function\(p,a,c,k,e,d\)[\s\S]*?\)\)'
        matches = re.findall(pattern, html_content)
        return matches

    def unpack_js(self, packed_js):
        """Unpack eval-packed JavaScript with better error handling."""
        try:
            # First try standard unpacking
            ctx = execjs.compile("""
            function unpack(code) {
                var env = {
                    eval: function(c) { result = c; },
                    window: {},
                    document: {}
                };
                var result;
                eval("with(env) {" + code + "}");
                return result;
            }
            """)
            unpacked = ctx.call("unpack", packed_js)
            
            # If that fails, try alternative unpacking approaches
            if not unpacked or len(unpacked) < 100:  # Too short to be meaningful
                # Try alternative unpacker
                ctx = execjs.compile("""
                function unPack(code) {
                    function indent(code) {
                        var tabs = 0, old=-1, add='';
                        for(var i=0;i<code.length;i++) {
                            if(code[i].indexOf("{") != -1) tabs++;
                            if(code[i].indexOf("}") != -1) tabs--;
                            
                            if(old != tabs) {
                                old = tabs;
                                add = "";
                                while (old > 0) {
                                    add += "\\t";
                                    old--;
                                }
                                old = tabs;
                            }
                            
                            code[i] = add + code[i];
                        }
                        return code;
                    }
                    
                    var env = {
                        eval: function(c) { code = c; },
                        window: {},
                        document: {}
                    };
                    
                    eval("with(env) {" + code + "}");
                    
                    code = (code+"").replace(/;/g, ";\\n").replace(/{/g, "\\n{\\n").replace(/}/g, "\\n}\\n").replace(/\\n;\\n/g, ";\\n").replace(/\\n\\n/g, "\\n");
                    
                    code = code.split("\\n");
                    code = indent(code);
                    
                    return code.join("\\n");
                }
                """)
                unpacked = ctx.call("unPack", packed_js)
                
            return unpacked
        except Exception as e:
            logger.error(f"Error unpacking JavaScript: {e}")
            return None

    def extract_m3u8_links(self, unpacked_js):
        """Extract m3u8 links from unpacked JavaScript with JWPlayer patterns."""
        # Pattern to match JWPlayer setup with sources array
        jwplayer_pattern = r'sources\s*:\s*\[([^\]]+)\]'
        jwplayer_matches = re.findall(jwplayer_pattern, unpacked_js, re.DOTALL)
        
        m3u8_links = []
        
        # If we found JWPlayer sources array
        if jwplayer_matches:
            sources_text = jwplayer_matches[0]
            # Now look for file URLs within the sources
            file_pattern = r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']'
            m3u8_links = re.findall(file_pattern, sources_text)
        
        # Also look for general m3u8 URLs as fallback
        general_pattern = r'https?://[^"\'\s]+\.m3u8[^"\'\s]*'
        general_links = re.findall(general_pattern, unpacked_js)
        m3u8_links.extend(general_links)
        
        # Filter out duplicates while preserving order
        unique_links = []
        seen = set()
        for link in m3u8_links:
            if link not in seen:
                unique_links.append(link)
                seen.add(link)
                
        return unique_links
    
    def extract_sources_from_js(self, unpacked_js):
        """Extract source objects with quality information if available."""
        try:
            # Try to extract sources array or object with quality info
            sources_pattern = r'sources\s*:\s*\[(.*?)\]'
            sources_match = re.search(sources_pattern, unpacked_js, re.DOTALL)
            
            if sources_match:
                sources_text = sources_match.group(1)
                # Extract individual source objects
                source_objects = []
                
                # Look for file and label pairs
                file_pattern = r'file\s*:\s*["\']([^"\']+)["\']'
                label_pattern = r'label\s*:\s*["\']([^"\']+)["\']'
                
                file_matches = re.findall(file_pattern, sources_text)
                label_matches = re.findall(label_pattern, sources_text)
                
                # Match files with labels if possible
                if len(file_matches) == len(label_matches):
                    for i in range(len(file_matches)):
                        if '.m3u8' in file_matches[i]:
                            source_objects.append({
                                'file': file_matches[i],
                                'label': label_matches[i]
                            })
                else:
                    # Just extract files
                    for file in file_matches:
                        if '.m3u8' in file:
                            source_objects.append({
                                'file': file
                            })
                
                return source_objects
        except Exception as e:
            logger.error(f"Error extracting sources: {e}")
        
        return []

    def get_m3u8_from_source(self, url):
        """Main function to fetch the page and extract m3u8 links."""
        try:
            # Extract slug from URL
            slug = self.extract_slug_from_url(url)
            if not slug:
                return M3U8Response(success=False, error="Could not extract slug from URL")
                
            logger.info(f"Extracted slug: {slug}")
            
            # Fetch the page with improved timeout and retry handling
            logger.info(f"Fetching URL: {url}")
            try:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
            except requests.Timeout:
                logger.error("Request timed out, retrying once with increased timeout")
                response = self.session.get(url, timeout=60)
                response.raise_for_status()
            except requests.ConnectionError as ce:
                logger.error(f"Connection error: {ce}")
                # Retry with different headers
                backup_headers = {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                }
                logger.info("Retrying with alternate headers")
                response = requests.get(url, headers=backup_headers, timeout=60)
                response.raise_for_status()
            
            # Find eval-packed JavaScript
            packed_scripts = self.find_eval_packed_js(response.text)
            logger.info(f"Found {len(packed_scripts)} packed scripts")
            
            # Debugging for empty response or no scripts
            if not packed_scripts:
                logger.warning("No packed scripts found, response length: " + str(len(response.text)))
                # Save a sample of the response for debugging
                sample = response.text[:500] + "..." if len(response.text) > 500 else response.text
                logger.debug(f"Response sample: {sample}")
                return M3U8Response(success=False, error="No eval-packed scripts found")
            
            # Process each packed script
            all_m3u8_links = []
            
            for i, packed_script in enumerate(packed_scripts):
                logger.info(f"Processing packed script {i+1}")
                
                # Debug info for script size
                script_size = len(packed_script)
                logger.debug(f"Script {i+1} size: {script_size} bytes")
                
                # Unpack the JavaScript
                unpacked_js = self.unpack_js(packed_script)
                if not unpacked_js:
                    logger.warning(f"Failed to unpack script {i+1}")
                    # Try a third approach as last resort
                    try:
                        logger.info("Attempting third unpacking method for script")
                        # Simplified unpacker for highly obfuscated scripts
                        ctx = execjs.compile("""
                        function simpleUnpack(code) {
                            try {
                                var sandbox = {
                                    result: '',
                                    console: { log: function(x) { this.result += x + '\\n'; } }
                                };
                                
                                // Replace eval with console.log to capture output
                                code = code.replace(/\\beval\\b/g, 'console.log');
                                
                                // Execute with sandbox
                                Function('console', code)(sandbox.console);
                                
                                return sandbox.result;
                            } catch(e) {
                                return "Error: " + e.toString();
                            }
                        }
                        """)
                        unpacked_js = ctx.call("simpleUnpack", packed_script)
                        if unpacked_js and not unpacked_js.startswith("Error:"):
                            logger.info("Third unpacking method succeeded")
                        else:
                            logger.warning("Third unpacking method also failed")
                            continue
                    except Exception as e:
                        logger.error(f"Third unpacker error: {e}")
                        continue
                
                # Extract m3u8 links
                m3u8_links = self.extract_m3u8_links(unpacked_js)
                all_m3u8_links.extend(m3u8_links)
                
                logger.info(f"Found {len(m3u8_links)} m3u8 links in script {i+1}")
                
                # If no m3u8 links found, try a more aggressive pattern
                if not m3u8_links:
                    logger.info("No links found with standard patterns, trying more aggressive patterns")
                    # Look for any URL with .m3u8 in it
                    aggressive_pattern = r'["\']?(https?://[^"\'\s]+?\.m3u8[^"\'\s]*)["\']?'
                    aggressive_links = re.findall(aggressive_pattern, unpacked_js)
                    logger.info(f"Found {len(aggressive_links)} links with aggressive pattern")
                    all_m3u8_links.extend(aggressive_links)
            
            # Remove duplicates from all found links
            unique_m3u8_links = list(dict.fromkeys(all_m3u8_links))
            
            # If no links found at all, return appropriate error
            if not unique_m3u8_links:
                return M3U8Response(
                    success=False,
                    slug=slug,
                    total_packed_scripts=len(packed_scripts),
                    error="No m3u8 links found in any scripts"
                )
            
            return M3U8Response(
                success=True,
                slug=slug,
                total_packed_scripts=len(packed_scripts),
                m3u8_links=unique_m3u8_links,
                count=len(unique_m3u8_links)
            )
            
        except requests.RequestException as e:
            logger.error(f"Request error: {e}")
            return M3U8Response(success=False, error=f"Request error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return M3U8Response(success=False, error=f"Unexpected error: {str(e)}")


# Initialize scraper
scraper = VideoScraper()


@app.get("/", response_model=dict)
async def root():
    return {
        "message": "M3U8 Scraper API", 
        "version": "1.0.0",
        "endpoints": {
            "/scrape": "Scrape m3u8 links from a URL",
            "/scrape/{slug}": "Scrape m3u8 links using a video slug"
        }
    }


@app.get("/scrape", response_model=M3U8Response)
async def scrape_url(url: str = Query(..., description="URL to scrape for m3u8 links")):
    """
    Scrape a URL for m3u8 links from eval-packed JavaScript.
    """
    try:
        result = scraper.get_m3u8_from_source(url)
        return result
    except Exception as e:
        logger.error(f"Error in scrape endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scrape/{slug}", response_model=M3U8Response)
async def scrape_by_slug(slug: str):
    """
    Scrape a video by its slug/ID.
    """
    try:
        url = f"https://zpjid.com/bkg/{slug}?ref=animedub.pro"
        result = scraper.get_m3u8_from_source(url)
        return result
    except Exception as e:
        logger.error(f"Error in scrape_by_slug endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    import os
    
    # Get port from environment variable (for deployment platforms like Render)
    port = int(os.environ.get("PORT", 8000))
    
    # Configure uvicorn with restart on file changes for development
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        log_level="info",
        timeout_keep_alive=120,  # Increase keep-alive timeout
        proxy_headers=True       # Trust X-Forwarded-* headers for proper IP handling
    )
