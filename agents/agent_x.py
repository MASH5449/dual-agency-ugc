import os
import time
import json
import tweepy

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")
X_CONSUMER_KEY = os.getenv("X_CONSUMER_KEY")
X_CONSUMER_SECRET = os.getenv("X_CONSUMER_SECRET")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET")

STRIPE_150_LINK = os.getenv("STRIPE_150_LINK", "https://stripe.com")
PORTFOLIO_LINK = os.getenv("PORTFOLIO_LINK", "https://example.com")

def run_twitter_bot():
    if not all([X_BEARER_TOKEN, X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET]):
        print("[Agent X] Credentials missing. Sleeping thread.")
        return

    client = tweepy.Client(
        bearer_token=X_BEARER_TOKEN,
        consumer_key=X_CONSUMER_KEY,
        consumer_secret=X_CONSUMER_SECRET,
        access_token=X_ACCESS_TOKEN,
        access_token_secret=X_ACCESS_TOKEN_SECRET
    )

    processed_file = "processed_tweets.json"
    if not os.path.exists(processed_file):
        with open(processed_file, "w") as f:
            json.dump([], f)

    query = '"need UGC" OR "looking for UGC creators" -is:retweet'
    
    print("[Agent X] Listening for Twitter/X leads...")
    while True:
        try:
            with open(processed_file, "r") as f:
                processed_ids = json.load(f)

            tweets = client.search_recent_tweets(query=query, max_results=10)
            if tweets.data:
                for tweet in tweets.data:
                    if tweet.id not in processed_ids:
                        reply_text = f"Hey! We render 24hr AI UGC ad concepts for DTC brands. See samples: {PORTFOLIO_LINK} | Get a 24hr test ad delivered tomorrow: {STRIPE_150_LINK}"
                        client.create_tweet(in_reply_to_tweet_id=tweet.id, text=reply_text)
                        
                        processed_ids.append(tweet.id)
                        print(f"[Agent X] Replied to tweet ID: {tweet.id}")

            with open(processed_file, "w") as f:
                json.dump(processed_ids, f)

        except Exception as e:
            print(f"[Agent X] Loop notice: {e}")

        time.sleep(300)

if __name__ == "__main__":
    run_twitter_bot()
