import os
import time
import spacy

# Safe NLP loading
nlp = None
try:
    nlp = spacy.load("en_core_web_sm")
    print("loaded spaCy model successfully.")
except OSError:
    print("⚠️ spaCy model not found. Semantic features limited.")