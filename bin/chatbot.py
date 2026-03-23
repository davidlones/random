import openai
import os

def chat():
  print("Hello! How can I help you today?")
  while True:
    user_input = input("> ")
    if user_input == "exit":
      break

    response = openai.Completion.create(
      engine="text-davinci-002",
      prompt=f"{user_input}\n",
      max_tokens=1024,
      temperature=0.7,
    ).get("choices")[0].get("text")

    print(response)

if __name__ == "__main__":
  chat()
