import requests

url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"

response = requests.get(url)

data = response.json()

print(type(data))
print(data.keys())

print()
print(data)