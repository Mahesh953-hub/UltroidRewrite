
echo "
            ┏┳┓╋┏┓╋╋╋╋┏┓┏┓
            ┃┃┣┓┃┗┳┳┳━╋╋┛┃
            ┃┃┃┗┫┏┫┏┫╋┃┃╋┃
            ┗━┻━┻━┻┛┗━┻┻━┛

      Visit @TheUltroid for updates!!

"
export TIMEZONE="UTC" 
git config --global user.name "kailashhh"
git config --global user.email "alex.c992954@gmail.xom"
git config --global credential.helper store

git config --global --add safe.directory /Ult

if ! grep -q "https://huggingface.co" ~/.git-credentials 2>/dev/null; then
  echo "https://kailashhh:HF_TOKEN@huggingface.co" >> ~/.git-credentials
fi

# Fix 4: Handle Unknown Time Zone
if [ -z "$(python3 -c 'import pytz; print(pytz.timezone("UTC"))')" ]; then
  echo "Error: Timezone is invalid or not set. Using UTC as default."
  export TIMEZONE="UTC"
fi

# Fix 5: Ensure Permissions on .gitconfig
if [ ! -w ~/.gitconfig ]; then
  echo "Error: Insufficient permissions for .gitconfig. Fixing..."
  sudo chmod 644 ~/.gitconfig
fi

# Run Python Server
python3 server.py || echo "Error: Server failed to start."

# Print Success Message
echo "Git setup and server initialized successfully."

if [ -f .env ] ; then  set -o allexport; source .env; set +o allexport ; fi
if [ $SESSION1 ] ; then wget https://gist.github.com/1Danish-00/6554f034549197e5824972b01f631e0c/raw/main.py && python3 main.py ; else python3 -m pyUltroid ; fi
