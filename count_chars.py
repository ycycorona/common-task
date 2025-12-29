#!/usr/bin/env python3
import sys
import re
import string

def is_srt_timestamp(line):
    # Matches standard SRT timestamp: 00:00:00,000 --> 00:00:00,000
    # Also forgiving on format slightly (dots or commas)
    pattern = r'^\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s-->\s\d{1,2}:\d{2}:\d{2}[,.]\d{3}'
    return re.match(pattern, line.strip()) is not None

def count_stats(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        try:
             with open(file_path, 'r', encoding='gbk') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Error reading file {file_path}: {e}")
            return

    total_cjk_chars = 0
    total_english_words = 0
    
    # Unicode ranges for CJK (Chinese, Japanese, Korean)
    # Common CJK Unified Ideographs: 4E00-9FFF
    # Hiragana: 3040-309F
    # Katakana: 30A0-30FF
    cjk_pattern = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]')

    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            continue
            
        # Check if it is an SRT file and if this line is a timestamp
        if file_path.lower().endswith('.srt') or file_path.lower().endswith('.vtt'):
            if is_srt_timestamp(stripped_line):
                continue
            # Logic to skip simple integer indices often found in SRT? 
            # User specifically said "ignore time axis lines", but for a pure character count of "content", 
            # usually the index numbers are also noise. 
            # However, looking at the request strictness: "ignore time axis lines". 
            # Adding a check for pure integer lines in SRT context might be safer for "content" stats.
            if stripped_line.isdigit():
                continue

        # Count CJK Characters
        cjk_matches = cjk_pattern.findall(stripped_line)
        total_cjk_chars += len(cjk_matches)
        
        # Process for English/Western words
        # 1. Start with the original line
        # 2. Replace CJK chars with spaces (so they don't combine with English words)
        no_cjk = cjk_pattern.sub(' ', stripped_line)
        
        # 3. Replace punctuation with spaces
        # We include standard punctuation + fullwidth forms often visible in CJK text
        # But actually, regex \w matches [a-zA-Z0-9_] and unicode alphanumerics. 
        # Easier approach: simple split by non-alphanumeric.
        
        # Let's clean up: remove anything that is NOT (Latin alphanumeric).
        # We effectively want sequences of [a-zA-Z0-9]+
        # Note: We keep numbers as words (e.g. "2023"). User didn't specify, but usually they count as words/tokens.
        
        eng_words = re.findall(r'[a-zA-Z0-9]+', no_cjk)
        total_english_words += len(eng_words)

    print(f"File: {file_path}")
    print(f"CJK Characters: {total_cjk_chars}")
    print(f"English Words: {total_english_words}")
    print(f"Total: {total_cjk_chars + total_english_words}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 count_char.py <file_path>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    count_stats(file_path)
