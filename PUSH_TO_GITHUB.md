# Push Mantis to GitHub — exact commands

## Step 1: Create the repo on GitHub

Go to https://github.com/organizations/mantis-x/repositories/new

Settings to use:
- Repository name:  mantis
- Description:      Signal-to-execution AI system on Mantle. See the move. Make the move.
- Visibility:       Public
- Init:             DO NOT tick "Add a README" — we already have one
- Click "Create repository"

## Step 2: Push from your local machine

Download the zip from this conversation, unzip it, then run:

```bash
cd mantis

# If git isn't already initialised (it is if you unzipped our file):
git init
git add -A
git commit -m "feat: initial Mantis monorepo"
git branch -M main

# Add the remote and push
git remote add origin https://github.com/mantis-x/mantis.git
git push -u origin main
```

## Step 3: Protect main branch

After pushing, go to:
  https://github.com/mantis-x/mantis/settings/branches

Add a branch protection rule for `main`:
  - Require pull request before merging: OFF (you're solo, speed matters)
  - Require status checks to pass: ON (once you add CI)

## Step 4: Add repo description + topics

On the repo homepage click the gear icon next to "About":
- Description: Signal-to-execution AI system on Mantle. See the move. Make the move.
- Website: https://devhub.mantle.xyz (link to hackathon until you have a site)
- Topics:  mantle  defi  ai-agent  on-chain-analytics  web3  telegram-bot  byreal  erc8004  hackathon

Topics make the repo discoverable and show judges you know the ecosystem.

## Step 5: Pin to org profile

Go to https://github.com/mantis-x
Click "Customize your profile" → pin the mantis repo

## File to update before pushing
Replace placeholder values in .env.example if you've already deployed contracts:
  AUDIT_CONTRACT_ADDRESS=0x...
  AGENT_IDENTITY_CONTRACT_ADDRESS=0x...

Everything else can stay as-is — .env is in .gitignore so real keys won't be committed.
