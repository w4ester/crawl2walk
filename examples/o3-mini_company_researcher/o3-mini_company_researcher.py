import os
import json
import time
import requests
from dotenv import load_dotenv
from openai import OpenAI
from serpapi.google_search import GoogleSearch
from security import safe_requests

# ANSI color codes
class Colors:
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    MAGENTA = '\033[95m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

# Load environment variables
load_dotenv()

# Initialize clients
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
firecrawl_api_key = os.getenv("FIRECRAWL_API_KEY")
serp_api_key = os.getenv("SERP_API_KEY")

def search_google(query):
    """Search Google using SerpAPI and return top results."""
    print(f"{Colors.YELLOW}Searching Google for '{query}'...{Colors.RESET}")
    search = GoogleSearch({"q": query, "api_key": serp_api_key})
    return search.get_dict().get("organic_results", [])

def select_urls_with_r1(company, objective, serp_results):
    """
    Use R1's approach with o3-mini to select URLs from SERP results.
    Returns a list of URLs.
    """
    try:
        # Prepare the data for o3-mini (keeping R1's structure)
        serp_data = [{"title": r.get("title"), "link": r.get("link"), "snippet": r.get("snippet")} 
                     for r in serp_results if r.get("link")]

        response = client.chat.completions.create(
            model="o3-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a URL selector that always responds with valid JSON. You select URLs from the SERP results relevant to the company and objective. Your response must be a JSON object with a 'selected_urls' array property containing strings."
                },
                {
                    "role": "user",
                    "content": (
                        f"Company: {company}\n"
                        f"Objective: {objective}\n"
                        f"SERP Results: {json.dumps(serp_data)}\n\n"
                        "Return a JSON object with a property 'selected_urls' that contains an array "
                        "of URLs most likely to help meet the objective. Add a /* to the end of the URL if you think it should search all of the pages in the site. Do not return any social media links. For example: {\"selected_urls\": [\"https://example.com\", \"https://example2.com\"]}"
                    )
                }
            ]
        )

        try:
            # First try to parse as JSON
            result = json.loads(response.choices[0].message.content)
            if isinstance(result, dict) and "selected_urls" in result:
                urls = result["selected_urls"]
            else:
                # If JSON doesn't have the expected structure, fall back to text parsing
                response_text = response.choices[0].message.content
                urls = [line.strip() for line in response_text.split('\n') 
                       if line.strip().startswith(('http://', 'https://'))]
        except json.JSONDecodeError:
            # If JSON parsing fails, fall back to text parsing
            response_text = response.choices[0].message.content
            urls = [line.strip() for line in response_text.split('\n') 
                   if line.strip().startswith(('http://', 'https://'))]

        # Clean up URLs - remove wildcards and trailing slashes
        cleaned_urls = [url.replace('/*', '').rstrip('/') for url in urls]
        cleaned_urls = [url for url in cleaned_urls if url]

        if not cleaned_urls:
            print(f"{Colors.YELLOW}No valid URLs found.{Colors.RESET}")
            return []

        print(f"{Colors.CYAN}Selected URLs for extraction:{Colors.RESET}")
        for url in cleaned_urls:
            print(f"- {url}")

        return cleaned_urls

    except Exception as e:
        print(f"{Colors.RED}Error selecting URLs: {e}{Colors.RESET}")
        return []

def extract_company_info(urls, prompt, company, api_key):
    """Use requests to call Firecrawl's extract endpoint with selected URLs."""
    print(f"{Colors.YELLOW}Extracting structured data from the provided URLs using Firecrawl...{Colors.RESET}")
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}'
    }
    
    payload = {
        "urls": urls,
        "prompt": prompt + " for " + company,
        "enableWebSearch": True
    }
    
    try:
        response = requests.post(
            "https://api.firecrawl.dev/v1/extract",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        data = response.json()
        
        if not data.get('success'):
            print(f"{Colors.RED}API returned error: {data.get('error', 'No error message')}{Colors.RESET}")
            return None
        
        extraction_id = data.get('id')
        if not extraction_id:
            print(f"{Colors.RED}No extraction ID found in response.{Colors.RESET}")
            return None

        return poll_firecrawl_result(extraction_id, api_key)

    except requests.exceptions.RequestException as e:
        print(f"{Colors.RED}Request failed: {e}{Colors.RESET}")
        return None
    except json.JSONDecodeError as e:
        print(f"{Colors.RED}Failed to parse response: {e}{Colors.RESET}")
        return None
    except Exception as e:
        print(f"{Colors.RED}Failed to extract data: {e}{Colors.RESET}")
        return None

def poll_firecrawl_result(extraction_id, api_key, interval=5, max_attempts=36):
    """Poll Firecrawl API to get the extraction result."""
    url = f"https://api.firecrawl.dev/v1/extract/{extraction_id}"
    headers = {
        'Authorization': f'Bearer {api_key}'
    }

    for attempt in range(1, max_attempts + 1):
        try:
            response = safe_requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get('success') and data.get('data'):
                print(f"{Colors.GREEN}Data successfully extracted:{Colors.RESET}")
                print(json.dumps(data['data'], indent=2))
                return data['data']
            elif data.get('success') and not data.get('data'):
                time.sleep(interval)
            else:
                print(f"{Colors.RED}API Error: {data.get('error', 'No error message provided')}{Colors.RESET}")
                return None

        except requests.exceptions.RequestException:
            return None
        except json.JSONDecodeError:
            return None
        except Exception:
            return None

    print(f"{Colors.RED}Max polling attempts reached. Extraction did not complete in time.{Colors.RESET}")
    return None

def main():
    company = input(f"{Colors.BLUE}Enter the company name: {Colors.RESET}")
    objective = input(f"{Colors.BLUE}Enter what information you want about the company: {Colors.RESET}")
    
    serp_results = search_google(f"{company}")
    if not serp_results:
        print(f"{Colors.RED}No search results found.{Colors.RESET}")
        return
    
    # Use R1's URL selection approach with o3-mini model
    selected_urls = select_urls_with_r1(company, objective, serp_results)
    
    if not selected_urls:
        print(f"{Colors.RED}No URLs were selected.{Colors.RESET}")
        return
    
    data = extract_company_info(selected_urls, objective, company, firecrawl_api_key)
    
    if data:
        print(f"{Colors.GREEN}Extraction completed successfully.{Colors.RESET}")
    else:
        print(f"{Colors.RED}Failed to extract the requested information. Try refining your prompt or choosing a different company.{Colors.RESET}")

if __name__ == "__main__":
    main()
