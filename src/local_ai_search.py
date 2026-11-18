import os
import re
import requests
import urllib.parse
from bs4 import BeautifulSoup

from settings_manager import load_settings
from wiki_server import configured_zim_path, get_wikipedia_summary, wikipedia_available

# --- Configuration ---
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"


def configured_ollama(settings=None):
    settings = load_settings() if settings is None else settings
    url = settings.get("ollama_url") or os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_URL
    if "://" not in url:
        url = "http://" + url
    model = settings.get("ollama_model") or os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    return url.rstrip("/"), model


def clean_paragraph_text(p_tag):
    """Removes citations and cleans up spacing and encoding."""
    for sup in p_tag.find_all(['sup', 'style', 'script']):
        sup.decompose()
    raw_text = p_tag.get_text(separator=" ", strip=True)
    return re.sub(r'\s+', ' ', raw_text)

def ask_ollama(model, prompt):
    """Sends a prompt to your local Ollama engine."""
    url, default_model = configured_ollama()
    payload = {"model": model or default_model, "prompt": prompt, "stream": False}
    try:
        response = requests.post(f"{url}/api/generate", json=payload, timeout=120)
        response.raise_for_status()
        return response.json().get("response", "Error generating response.")
    except Exception as e:
        return f"Ollama connection error: {e}"

def fetch_wikipedia_text(query):
    """Searches the Kiwix server and extracts clean text from the top 20 articles."""
    server = os.getenv("KIWIX_SERVER", "http://127.0.0.1:8080").rstrip("/")
    archive_id = os.getenv("KIWIX_ARCHIVE_ID", os.path.splitext(os.path.basename(configured_zim_path()))[0])
    search_url = f"{server}/search?" + urllib.parse.urlencode({"content": archive_id, "pattern": query})
    
    try:
        res = requests.get(search_url, timeout=10)
        res.encoding = 'utf-8'
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        
        extracted_text = ""
        links = soup.find_all('a', href=True)
        article_count = 0
        
        for link in links:
            href = link['href']
            if archive_id in href and "pattern=" not in href:
                content_href = href.replace("/viewer#", "/")
                if not content_href.startswith("/"):
                    content_href = "/" + content_href
                    
                article_url = f"{server}{content_href}"
                
                try:
                    article_res = requests.get(article_url, timeout=5)
                    article_res.encoding = 'utf-8'
                    article_soup = BeautifulSoup(article_res.text, 'html.parser')
                    
                    paragraphs = article_soup.find_all('p')
                    abstract_chunks = []
                    for p in paragraphs:
                        text = clean_paragraph_text(p)
                        if len(text) > 60:
                            abstract_chunks.append(text)
                        if len(abstract_chunks) >= 3:  # Grab up to 3 paragraphs per article
                            break
                    
                    abstract = " ".join(abstract_chunks)
                    
                    if abstract:
                        article_title = link.get_text(separator=" ", strip=True)
                        extracted_text += f"\n--- Article: {article_title} ---\n{abstract}\n"
                        article_count += 1
                        
                    if article_count >= 20:
                        break
                except requests.exceptions.RequestException:
                    continue
                    
        return extracted_text if extracted_text else "No relevant articles found in local Wikipedia."
        
    except Exception as e:
        return f"Error connecting to Kiwix: {e}"

def run_local_search(question):
    result = get_wikipedia_summary(question, max_paragraphs=3) if wikipedia_available() else {}
    context = result.get("summary", "") if result.get("found") else ""
    prompt = f"Answer this study question clearly. Use the Wikipedia excerpt if relevant.\nQuestion: {question}\nExcerpt: {context}"
    print(ask_ollama(None, prompt))


if __name__ == "__main__":
    print("Local AI Search Engine Initialized. Type 'exit' to quit.")
    while True:
        user_input = input("\nWhat would you like to know? ")
        if user_input.lower() == 'exit':
            break
        run_local_search(user_input)