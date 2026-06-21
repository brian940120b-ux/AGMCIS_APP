from database_service import get_account

a = get_account()

print("Balance =", a["balance"])
print("Trades =", a["trades"])
print("Wins =", a["wins"])
print("Losses =", a["losses"])
