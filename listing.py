from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List
from bs4 import BeautifulSoup
import requests
import uuid
import json
import os
import re
from math import radians, sin, cos, sqrt, atan2

TARGET_AREAS = {
    "Töölö": (60.1826, 24.9221),
    "Kallio": (60.1854, 24.9525),
    "Punavuori": (60.1595, 24.9384),
}

app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_FILE = "listings.json"

# Mount static folder
app.mount("/static", StaticFiles(directory="static"), name="static")

# Serve HTML at root
@app.get("/", response_class=FileResponse)
def serve_root():
    return FileResponse("static/index.html")

class Listing(BaseModel):
    id: str
    url: str
    address: str = ""
    price: str = ""
    area: str = ""
    floor: str = ""
    rooms: str = ""
    description: str = ""
    image_urls: List[str] = []
    latitude: float | None = None
    longitude: float | None = None
    nearest_target: str = ""
    distance_to_target_km: float | None = None


class URLInput(BaseModel):
    url: str

def load_data() -> List[Listing]:
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
        return [Listing(**item) for item in data]

def save_data(listings: List[Listing]):
    with open(DATA_FILE, "w") as f:
        json.dump([listing.dict() for listing in listings], f, indent=2)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def get_nearest_area(lat, lon):
    distances = {
        name: haversine(lat, lon, area_lat, area_lon)
        for name, (area_lat, area_lon) in TARGET_AREAS.items()
    }
    nearest = min(distances, key=distances.get)
    return nearest, round(distances[nearest], 2)

def scrape_listing(url: str) -> Listing:
    try:
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        # Address and rooms
        address_tag = soup.find("h1")
        raw_address = address_tag.get_text(strip=True) if address_tag else ""
        parts = raw_address.split("●")
        address = parts[0].strip()
        rooms = parts[1].strip() if len(parts) > 1 else ""

        # Price
        price_tag = soup.find("span", string=lambda t: t and "€" in t)
        price = price_tag.get_text(strip=True) if price_tag else ""

        # Area and floor
        area = floor = ""
        for dt in soup.find_all("dt"):
            key = dt.get_text(strip=True)
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            val = dd.get_text(strip=True)
            if "Asuinpinta-ala" in key:
                area = val
            if "Kerros" in key:
                floor = val

        # Description
        description_tags = soup.find_all("p", class_="paragraph--keep-formatting")
        description = "\n\n".join(p.get_text(strip=True) for p in description_tags)

        # Images
        image_tags = soup.find_all("img")
        image_urls = [img["data-big"] for img in image_tags if img.has_attr("data-big")]


        # Extract coordinates from <listing-map-container>
        map_tag = soup.find("listing-map-container")
        latitude = longitude = None

        if map_tag:
            tag_str = str(map_tag)
            lat_match = re.search(r'\[latitude\]="([\d\.]+)"', tag_str)
            lon_match = re.search(r'\[longitude\]="([\d\.]+)"', tag_str)

            if lat_match and lon_match:
                latitude = float(lat_match.group(1))
                longitude = float(lon_match.group(1))

        # Determine nearest target area
        nearest_target = ""
        distance_to_target = None
        if latitude and longitude:
            distances = {
                name: haversine(latitude, longitude, coords[0], coords[1])
                for name, coords in TARGET_AREAS.items()
            }
            nearest_target = min(distances, key=distances.get)
            distance_to_target = round(distances[nearest_target], 2)

        return Listing(
            id=str(uuid.uuid4()),
            url=url,
            address=address,
            price=price,
            area=area,
            floor=floor,
            rooms=rooms,
            description=description,
            image_urls=image_urls,
            latitude=latitude,
            longitude=longitude,
            nearest_target=nearest_target,
            distance_to_target_km=distance_to_target
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scraping failed: {e}")

@app.get("/listings", response_model=List[Listing])
def get_listings():
    return load_data()

@app.post("/add_listing", response_model=Listing)
def add_listing(input: URLInput):
    listings = load_data()
    if any(l.url == input.url for l in listings):
        raise HTTPException(status_code=400, detail="Listing with this URL already exists.")
    new_listing = scrape_listing(input.url)
    listings.append(new_listing)
    save_data(listings)
    return new_listing

@app.delete("/delete_listing/{listing_id}", response_model=List[Listing])
def delete_listing(listing_id: str):
    data = load_data()
    updated_data = [l for l in data if l.id != listing_id]

    if len(updated_data) == len(data):
        raise HTTPException(status_code=404, detail="Listing not found")

    save_data(updated_data)
    return updated_data
