# 1) Disable any Brave/Proton repo files
for f in /etc/apt/sources.list.d/*brave* /etc/apt/sources.list.d/*proton*; do
  [ -e "$f" ] && sudo mv "$f" "$f.disabled"
done

# 2) Strip any lines from the main sources.list
sudo sed -i.bak -E '/brave-browser-apt-release\.s3\.brave\.com|repo\.protonvpn\.com/d' /etc/apt/sources.list

# 3) Remove their keyrings (safe to delete; they’re unused once repos are gone)
sudo rm -f /usr/share/keyrings/brave-browser-archive-keyring.gpg \
            /usr/share/keyrings/protonvpn-archive-keyring.asc \
            /etc/apt/trusted.gpg.d/*brave* \
            /etc/apt/trusted.gpg.d/*proton*

# 4) Refresh
sudo apt update
