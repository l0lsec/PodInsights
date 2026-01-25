"""Stock image fetching from free APIs (Unsplash, Pexels, Pixabay)."""

import os
import re
import logging
import requests
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# API Keys from environment
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")


def extract_keywords_from_text(text: str, max_keywords: int = 3) -> str:
    """Extract search keywords from post text.
    
    Prioritizes hashtags, then falls back to significant words.
    
    Parameters
    ----------
    text : str
        The post content to extract keywords from.
    max_keywords : int
        Maximum number of keywords to return.
        
    Returns
    -------
    str
        Space-separated keywords for image search.
    """
    # First try to extract hashtags
    hashtags = re.findall(r'#(\w+)', text)
    if hashtags:
        # Convert camelCase/PascalCase hashtags to words
        keywords = []
        for tag in hashtags[:max_keywords]:
            # Split on capital letters: "ArtificialIntelligence" -> "Artificial Intelligence"
            words = re.sub(r'([a-z])([A-Z])', r'\1 \2', tag)
            keywords.append(words.lower())
        return ' '.join(keywords)
    
    # Remove URLs, mentions, and special characters
    clean_text = re.sub(r'https?://\S+', '', text)
    clean_text = re.sub(r'@\w+', '', clean_text)
    clean_text = re.sub(r'[^\w\s]', ' ', clean_text)
    
    # Common stop words to filter out
    stop_words = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
        'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
        'this', 'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it',
        'we', 'they', 'what', 'which', 'who', 'whom', 'when', 'where', 'why',
        'how', 'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
        'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so',
        'than', 'too', 'very', 'just', 'your', 'our', 'my', 'their', 'its',
        'about', 'into', 'through', 'during', 'before', 'after', 'above',
        'below', 'between', 'under', 'again', 'further', 'then', 'once',
        'here', 'there', 'any', 'new', 'get', 'got', 'like', 'also', 'much',
        'even', 'well', 'back', 'being', 'going', 'make', 'way', 'know',
    }
    
    # Extract significant words
    words = clean_text.lower().split()
    significant = [w for w in words if len(w) > 3 and w not in stop_words]
    
    # Return top keywords
    return ' '.join(significant[:max_keywords]) if significant else 'professional business'


def search_unsplash(query: str, per_page: int = 5) -> List[Dict[str, Any]]:
    """Search Unsplash for images.
    
    Parameters
    ----------
    query : str
        Search query.
    per_page : int
        Number of results to return.
        
    Returns
    -------
    List[Dict]
        List of image results with urls and metadata.
    """
    if not UNSPLASH_ACCESS_KEY:
        logger.warning("UNSPLASH_ACCESS_KEY not set")
        return []
    
    try:
        response = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            params={
                "query": query,
                "per_page": per_page,
                "orientation": "landscape",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        
        results = []
        for photo in data.get("results", []):
            results.append({
                "id": photo["id"],
                "url": photo["urls"]["regular"],
                "thumb": photo["urls"]["thumb"],
                "small": photo["urls"]["small"],
                "description": photo.get("description") or photo.get("alt_description", ""),
                "photographer": photo["user"]["name"],
                "photographer_url": photo["user"]["links"]["html"],
                "source": "unsplash",
                "source_url": photo["links"]["html"],
            })
        return results
        
    except requests.RequestException as e:
        logger.error(f"Unsplash API error: {e}")
        return []


def search_pexels(query: str, per_page: int = 5) -> List[Dict[str, Any]]:
    """Search Pexels for images.
    
    Parameters
    ----------
    query : str
        Search query.
    per_page : int
        Number of results to return.
        
    Returns
    -------
    List[Dict]
        List of image results with urls and metadata.
    """
    if not PEXELS_API_KEY:
        logger.debug("PEXELS_API_KEY not set")
        return []
    
    try:
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={
                "query": query,
                "per_page": per_page,
                "orientation": "landscape",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        
        results = []
        for photo in data.get("photos", []):
            results.append({
                "id": str(photo["id"]),
                "url": photo["src"]["large"],
                "thumb": photo["src"]["tiny"],
                "small": photo["src"]["medium"],
                "description": photo.get("alt", ""),
                "photographer": photo["photographer"],
                "photographer_url": photo["photographer_url"],
                "source": "pexels",
                "source_url": photo["url"],
            })
        return results
        
    except requests.RequestException as e:
        logger.error(f"Pexels API error: {e}")
        return []


def search_pixabay(query: str, per_page: int = 5) -> List[Dict[str, Any]]:
    """Search Pixabay for images.
    
    Parameters
    ----------
    query : str
        Search query.
    per_page : int
        Number of results to return.
        
    Returns
    -------
    List[Dict]
        List of image results with urls and metadata.
    """
    if not PIXABAY_API_KEY:
        logger.debug("PIXABAY_API_KEY not set")
        return []
    
    try:
        response = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": PIXABAY_API_KEY,
                "q": query,
                "per_page": per_page,
                "image_type": "photo",
                "orientation": "horizontal",
                "safesearch": "true",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        
        results = []
        for photo in data.get("hits", []):
            results.append({
                "id": str(photo["id"]),
                "url": photo["largeImageURL"],
                "thumb": photo["previewURL"],
                "small": photo["webformatURL"],
                "description": photo.get("tags", ""),
                "photographer": photo["user"],
                "photographer_url": f"https://pixabay.com/users/{photo['user']}-{photo['user_id']}/",
                "source": "pixabay",
                "source_url": photo["pageURL"],
            })
        return results
        
    except requests.RequestException as e:
        logger.error(f"Pixabay API error: {e}")
        return []


def search_stock_images(query: str, per_page: int = 5) -> List[Dict[str, Any]]:
    """Search all configured stock image APIs.
    
    Tries Unsplash first, then Pexels, then Pixabay.
    Returns results from the first API that succeeds.
    
    Parameters
    ----------
    query : str
        Search query.
    per_page : int
        Number of results to return.
        
    Returns
    -------
    List[Dict]
        List of image results with urls and metadata.
    """
    # Try APIs in order of preference
    if UNSPLASH_ACCESS_KEY:
        results = search_unsplash(query, per_page)
        if results:
            return results
    
    if PEXELS_API_KEY:
        results = search_pexels(query, per_page)
        if results:
            return results
    
    if PIXABAY_API_KEY:
        results = search_pixabay(query, per_page)
        if results:
            return results
    
    logger.warning("No stock image API configured or no results found")
    return []


def get_image_for_post(post_content: str) -> Optional[str]:
    """Get a stock image URL for a post based on its content.
    
    Parameters
    ----------
    post_content : str
        The post text content.
        
    Returns
    -------
    Optional[str]
        Image URL if found, None otherwise.
    """
    keywords = extract_keywords_from_text(post_content)
    logger.info(f"Searching stock images for keywords: {keywords}")
    
    results = search_stock_images(keywords, per_page=1)
    if results:
        return results[0]["url"]
    
    return None


def get_images_for_post(post_content: str, count: int = 5) -> List[Dict[str, Any]]:
    """Get multiple stock image options for a post.
    
    Parameters
    ----------
    post_content : str
        The post text content.
    count : int
        Number of images to return.
        
    Returns
    -------
    List[Dict]
        List of image results with urls and metadata.
    """
    keywords = extract_keywords_from_text(post_content)
    logger.info(f"Searching stock images for keywords: {keywords}")
    
    return search_stock_images(keywords, per_page=count)


def is_configured() -> bool:
    """Check if any stock image API is configured."""
    return bool(UNSPLASH_ACCESS_KEY or PEXELS_API_KEY or PIXABAY_API_KEY)


def get_configured_services() -> List[str]:
    """Get list of configured stock image services."""
    services = []
    if UNSPLASH_ACCESS_KEY:
        services.append("unsplash")
    if PEXELS_API_KEY:
        services.append("pexels")
    if PIXABAY_API_KEY:
        services.append("pixabay")
    return services
