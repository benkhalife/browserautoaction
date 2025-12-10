import json
import sys

import pandas as pd


def main():
    if len(sys.argv) != 2:
        print("Usage: python script.py <path_to_excel_file>")
        sys.exit(1)

    excel_path = sys.argv[1]

    try:
        # خواندن فایل اکسل بدون در نظر گرفتن هدر (سطر اول نادیده گرفته می‌شود)
        df = pd.read_excel(excel_path, header=None, skiprows=1)

        # حذف سطرهایی که هر دو ستون خالی هستند
        df.dropna(how="all", inplace=True)

        # اطمینان از اینکه حداقل دو ستون وجود دارد
        if df.shape[1] < 2:
            print("Error: Excel file must have at least two columns.")
            sys.exit(1)

        results = []
        for _, row in df.iterrows():
            question = str(row[0]).strip() if pd.notna(row[0]) else ""
            answer = str(row[1]).strip() if pd.notna(row[1]) else ""

            # اگر هر دو مقدار خالی بودند، این سطر را نادیده بگیر
            if not question and not answer:
                continue

            obj = {
                "type": "array",
                "tag": "section",
                "class": ".clsQuestion",
                "if_find_text_inside": question,
                "click": [{"tag": "label", "text": answer}],
                "sleep": 2,
            }
            results.append(obj)

        # چاپ خروجی به صورت JSON با فرمت زیبا
        print(json.dumps(results, indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"Error reading Excel file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
