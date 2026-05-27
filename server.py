import http.server, os

ROOT = "/Users/carloschavando/Documents/FoodOrderAgent"

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

if __name__ == "__main__":
    os.chdir(ROOT)
    http.server.test(HandlerClass=Handler, port=3457, bind="127.0.0.1")
