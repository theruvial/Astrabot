import random


class Gambling:
    def check_valid_bucks(bucks: int) -> bool:
        ## runs a query to see
        return True

    def slots(bet: int = 1) -> int:
        ## replace with emojis of fruits or astra stuff
        symbols_dict = {
            1: 2,
            2: 2,
            3: 2,
            4: 2,
            5: 3,
            6: 4,
            "*": 10,
        }
        spin_result = [random.choice(symbols_dict.keys) for _ in range(3)]
        set_result = {*spin_result} #convert to set
        if "*" in set_result:
            set_result.remove("*")
        if len(set_result) == 1:
            payout = symbols_dict[list(set_result)[0]] * bet
        else:
            payout = 0

        return payout

    def blackjack(bet: int = 1) -> int:
        suits = ["a","b","c","d"] # replace with emojis of suits
        values = ["A","K"] # replace with emojis of values
        deck = [suit + value for suit in suits for value in values]
        random.shuffle(deck)

        dealer = [deck.pop(), deck.pop()]
        you =  [deck.pop(), deck.pop()]

        def evaluate(hand) -> int:
            ## if in values[:] return 10
            ## else return index number
            ## special case for A
            return hand
