import os
import subprocess
import sys
from datetime import datetime


def main():
    # Get directory path from user
    directory = input("Enter directory path: ").strip()

    if not os.path.isdir(directory):
        print(f"Error: Directory '{directory}' does not exist.")
        sys.exit(1)

    # Prepare report data structure
    report = {
        "start_time": datetime.now(),
        "directory": os.path.abspath(directory),
        "successful": [],
        "failed": [],
        "end_time": None,
    }

    # Collect all files in the directory (non-recursive)
    files = [
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, f))
    ]

    if not files:
        print(f"No files found in directory: {directory}")
        sys.exit(0)

    print(f"\nProcessing {len(files)} files from: {directory}")
    print("-" * 50)

    # Process each file sequentially
    for file_path in files:
        filename = os.path.basename(file_path)
        print(f"\nProcessing: {filename}")

        try:
            # Execute workflow script with file path argument
            result = subprocess.run(
                [sys.executable, "workflow.py", "--file", file_path],
                capture_output=True,
                text=True,
                check=True,
            )
            report["successful"].append(
                {"file": file_path, "output": result.stdout.strip()}
            )
            print(f"✓ Success: {filename}")

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() or e.stdout.strip() or str(e)
            report["failed"].append({"file": file_path, "error": error_msg})
            print(f"✗ Failed: {filename}")
            print(
                f"  Error: {error_msg[:100]}..."
                if len(error_msg) > 100
                else f"  Error: {error_msg}"
            )

        except Exception as e:
            report["failed"].append({"file": file_path, "error": str(e)})
            print(f"✗ Failed: {filename}")
            print(f"  Unexpected error: {str(e)}")

    report["end_time"] = datetime.now()

    # Generate report
    generate_report(report)


def generate_report(report):
    report_path = (
        f"workflow_report_{report['start_time'].strftime('%Y%m%d_%H%M%S')}.txt"
    )

    with open(report_path, "w") as f:
        # Header
        f.write("=" * 60 + "\n")
        f.write(f"WORKFLOW PROCESSING REPORT\n")
        f.write("=" * 60 + "\n")
        f.write(f"Directory: {report['directory']}\n")
        f.write(f"Start Time: {report['start_time'].strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"End Time: {report['end_time'].strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Duration: {report['end_time'] - report['start_time']}\n")
        f.write(
            f"Files Processed: {len(report['successful']) + len(report['failed'])}\n"
        )
        f.write(f"Successful: {len(report['successful'])}\n")
        f.write(f"Failed: {len(report['failed'])}\n")
        f.write("-" * 60 + "\n\n")

        # Successful files
        f.write("SUCCESSFUL FILES:\n")
        f.write("-" * 60 + "\n")
        if report["successful"]:
            for item in report["successful"]:
                f.write(f"File: {item['file']}\n")
                if item["output"]:
                    f.write("Output:\n")
                    f.write(f"{item['output']}\n")
                f.write("-" * 40 + "\n")
        else:
            f.write("No successful executions\n\n")

        # Failed files
        f.write("\nFAILED FILES:\n")
        f.write("-" * 60 + "\n")
        if report["failed"]:
            for item in report["failed"]:
                f.write(f"File: {item['file']}\n")
                f.write(f"Error:\n{item['error']}\n")
                f.write("-" * 40 + "\n")
        else:
            f.write("No failures\n")

    # Print completion summary
    print("\n" + "=" * 50)
    print("PROCESSING COMPLETE")
    print("=" * 50)
    print(f"Total files processed: {len(report['successful']) + len(report['failed'])}")
    print(f"Successful: {len(report['successful'])}")
    print(f"Failed: {len(report['failed'])}")
    print(f"\nDetailed report saved to: {report_path}")


if __name__ == "__main__":
    main()
