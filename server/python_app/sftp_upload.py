import paramiko
import os

# Configuration for the SFTP server.
#
# Modified 2026-09 (iteration 5): the password used to be a literal here, in a
# public repository, fronting students' biometric data. Host, port, user and
# password now come from the environment — put them in the repo's .env (loaded by
# src/session.py, see .env.example) or export them in the shell. The defaults
# below are the non-secret parts only.
DEFAULT_SFTP_HOST = os.environ.get("PLAYSMART_SFTP_HOST", "10.4.28.2")
DEFAULT_SFTP_PORT = int(os.environ.get("PLAYSMART_SFTP_PORT", "2422"))
DEFAULT_SFTP_USERNAME = os.environ.get("PLAYSMART_SFTP_USER", "localhost")
DEFAULT_SFTP_PASSWORD = os.environ.get("PLAYSMART_SFTP_PASSWORD", "")

def upload_file_to_sftp(local_file_path, dest_directory,
                        sftp_host=DEFAULT_SFTP_HOST,
                        sftp_port=DEFAULT_SFTP_PORT,
                        sftp_username=DEFAULT_SFTP_USERNAME,
                        sftp_password=DEFAULT_SFTP_PASSWORD
                        ):
    
    """
    Upload a single file to the SFTP server.

    Args:
        local_file_path (str): Full path of the local file to upload.
        dest_directory (str): Remote directory on the SFTP server.
        sftp_host (str): SFTP server host.
        sftp_port (int): SFTP server port.
        sftp_username (str): Username for SFTP server.
        sftp_password (str): Password for SFTP server.
    """
    if not sftp_password:
        print("PLAYSMART_SFTP_PASSWORD is not set - nothing uploaded, file kept locally: "
              f"{local_file_path}")
        return
    try:
        # Initialize SFTP client
        transport = paramiko.Transport((sftp_host, sftp_port))
        transport.connect(username=sftp_username, password=sftp_password)
        sftp = paramiko.SFTPClient.from_transport(transport)

        # Ensure the destination directory exists
        try:
            sftp.chdir(dest_directory)
        except IOError:
            print(f"Destination directory {dest_directory} does not exist.")
            raise

        # Upload the file
        file_name = os.path.basename(local_file_path)
        remote_file_path = os.path.join(dest_directory, file_name)
        sftp.put(local_file_path, remote_file_path)
        print(f"Uploaded {file_name} to {remote_file_path}")
        # os.remove(local_file_path) removes the file after uploading

        sftp.close()
        transport.close()
    except Exception as e:
        print(f"An error occurred while uploading to the SFTP server: {e}")
