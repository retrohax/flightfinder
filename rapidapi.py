from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import json
import logging

def rapidapi_request(api_url, api_host, api_key, icao: str):
	if not api_url or not api_host or not api_key or not icao:
		logging.error("Missing required parameters for RapidAPI request")
		return None
	url = f"{api_url.rstrip('/')}/hex/{icao}/"
	request = Request(url)
	request.add_header("x-rapidapi-key", api_key)
	request.add_header("x-rapidapi-host", api_host)
	request.add_header("accept", "application/json")

	try:
		with urlopen(request, timeout=20) as response:
			return json.load(response)
	except HTTPError as exc:
		logging.error("Failed to fetch for %s: HTTP %s %s", icao, exc.code, exc.reason)
	except URLError as exc:
		reason = getattr(exc, "reason", str(exc))
		logging.error("Failed to fetch for %s: network error: %s", icao, reason)
	except TimeoutError:
		logging.error("Failed to fetch for %s: request timed out", icao)
	except json.JSONDecodeError as exc:
		logging.error(
			"Failed to fetch snapshot for %s: invalid JSON at line %s column %s",
			icao,
			exc.lineno,
			exc.colno,
		)
	except Exception as exc:
		logging.error("Failed to fetch snapshot for %s: %s", icao, exc)

	return None


def _fetch_json(url, api_host, api_key, label):
	request = Request(url)
	request.add_header("x-rapidapi-key", api_key)
	request.add_header("x-rapidapi-host", api_host)
	request.add_header("accept", "application/json")
	try:
		with urlopen(request, timeout=30) as response:
			return json.load(response)
	except HTTPError as exc:
		logging.error("Failed to fetch %s: HTTP %s %s", label, exc.code, exc.reason)
	except URLError as exc:
		logging.error("Failed to fetch %s: network error: %s", label, getattr(exc, "reason", str(exc)))
	except TimeoutError:
		logging.error("Failed to fetch %s: request timed out", label)
	except json.JSONDecodeError as exc:
		logging.error("Failed to fetch %s: invalid JSON at line %s column %s", label, exc.lineno, exc.colno)
	except Exception as exc:
		logging.error("Failed to fetch %s: %s", label, exc)
	return None


def rapidapi_area_request(api_url, api_host, api_key, lat, lon, dist_nm):
	"""Every aircraft within dist_nm (max 250) of a point: {"ac": [...], "now": ms, ...}"""
	if not api_url or not api_host or not api_key:
		logging.error("Missing required parameters for RapidAPI request")
		return None
	url = f"{api_url.rstrip('/')}/lat/{lat}/lon/{lon}/dist/{dist_nm}/"
	return _fetch_json(url, api_host, api_key, f"area {lat},{lon} r={dist_nm}nm")
