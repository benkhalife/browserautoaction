import argparse
import json
import os
import re
from typing import Any, Dict, List, Union


def merge_json_files(args: argparse.Namespace) -> bool:
    directory = args.input_dir
    output_path = args.output
    excel_file = args.excel

    if not os.path.isdir(directory):
        print(f"Error: The specified path '{directory}' is not a valid directory!")
        return False

    # Validate output path has .json extension
    if not output_path.lower().endswith(".json"):
        print("Error: Output file must have a .json extension!")
        return False

    # Get JSON files with numeric names
    json_files = [
        f
        for f in os.listdir(directory)
        if f.lower().endswith(".json") and re.match(r"^\d+\.json$", f, re.IGNORECASE)
    ]

    if not json_files:
        print("No valid JSON files with numeric names found in the directory!")
        return False

    # Sort files numerically
    sorted_files = sorted(json_files, key=lambda x: int(os.path.splitext(x)[0]))
    print(f"\nFound {len(sorted_files)} valid JSON files")
    print("Processing files in order:", ", ".join(sorted_files))

    merged_data: List[Dict[str, Any]] = []
    errors = []
    warnings = []

    # Read and merge files
    for file_name in sorted_files:
        file_path = os.path.join(directory, file_name)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = json.load(f)

                if not isinstance(content, list):
                    errors.append(
                        f"File {file_name} does not contain a valid JSON array"
                    )
                    continue

                # Special handling when Excel mode is active
                if (
                    excel_file
                    and content
                    and isinstance(content[0], dict)
                    and content[0].get("type") == "group_excel"
                ):
                    if len(content) > 1:
                        warnings.append(
                            f"File {file_name} has additional elements after group_excel object. Only actions will be used."
                        )

                    actions = content[0].get("actions", [])
                    if not isinstance(actions, list):
                        errors.append(
                            f"Invalid 'actions' field in group_excel object in {file_name}"
                        )
                    else:
                        merged_data.extend(actions)
                        print(
                            f"✅ Processed {file_name} as group_excel (extracted {len(actions)} actions)"
                        )
                else:
                    merged_data.extend(content)
                    print(
                        f"✅ Successfully processed {file_name} ({len(content)} items)"
                    )

        except json.JSONDecodeError as e:
            errors.append(f"JSON decode error in {file_name}: {str(e)}")
        except Exception as e:
            errors.append(f"Error processing {file_name}: {str(e)}")

    # Report warnings if any
    if warnings:
        print("\n⚠️ Warnings encountered:")
        for i, warn in enumerate(warnings, 1):
            print(f"  {i}. {warn}")

    # Report errors if any
    if errors:
        print("\n❌ Errors encountered:")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}")
        if not merged_data:
            print("No valid data to merge. Aborting.")
            return False

    # Wrap in group_excel structure if Excel mode is active
    final_data: Union[List, List[Dict[str, Any]]] = merged_data
    if excel_file:
        final_data = [
            {
                "type": "group_excel",
                "file": excel_file,
                "start_row": 2,
                "actions": merged_data,
            }
        ]
        print(f"\nℹ️ Wrapped {len(merged_data)} items in group_excel structure")

    # Save merged data
    try:
        output_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(output_dir, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)

        print(f"\n✅ Successfully saved output to: {output_path}")
        print(f"Total items: {len(merged_data)}")
        print(f"File size: {os.path.getsize(output_path) / 1024:.2f} KB")
    except Exception as e:
        print(f"\n❌ Error saving output file: {str(e)}")
        return False

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Merge JSON files with optional Excel grouping"
    )
    parser.add_argument(
        "--input_dir", required=True, help="Directory containing JSON files"
    )
    parser.add_argument(
        "--output", required=True, help="Output file path (must end with .json)"
    )
    parser.add_argument("--excel", help="Excel file path for group_excel wrapper mode")

    args = parser.parse_args()

    print("=" * 50)
    print("JSON FILES MERGER")
    print("=" * 50)

    success = merge_json_files(args)

    print("\nProcess completed.")
    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
