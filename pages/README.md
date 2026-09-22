# Chatbot Pages

This repository contains the source files for the public AI4DRR Chatbot website:

https://ai4drr.github.io/chatbot/

## How It Works

Changes committed to the `main` branch are automatically deployed to the `pages/` directory of the main Chatbot repository.

Deployment flow:

    AI4DRR/chatbot-pages
            ↓
      GitHub Actions
            ↓
    AI4DRR/chatbot/pages
            ↓
       GitHub Pages
            ↓
    https://ai4drr.github.io/chatbot/

There is no need to manually copy website files to the main Chatbot repository.

## Editing the Website

Website pages and assets can be created and edited directly in this repository.

For example, you can add:

    index.html
    about.html
    contact.html
    css/
    js/
    images/

After the changes are committed to `main`, they will be automatically deployed to the public website.

## Adding a New Page

For example, create:

    about.html

Commit the file to `main`.

Once deployment completes, the page will be available at:

    https://ai4drr.github.io/chatbot/about.html

The same process applies to HTML pages, stylesheets, JavaScript, images, and other static website assets.

## Example Structure

The website structure is flexible. The example below shows one possible way to organize the files:

    chatbot-pages/
    ├── .github/
    │   └── workflows/
    │       └── deploy.yml    # Automatic deployment - do not modify
    ├── index.html
    ├── about.html
    ├── css/
    │   └── style.css
    ├── js/
    │   └── main.js
    └── images/
        └── logo.png

This is only an example. Website files and directories can be organized as needed.

## Important

Do **not** modify or delete:

    .github/workflows/deploy.yml

This workflow handles the automatic deployment from this repository to the main Chatbot repository.

Also, do not commit passwords, API keys, access tokens, or other secrets to this repository.

## Deleting or Renaming Files

The deployment mirrors the website files in this repository to the published `pages/` directory.

If a website file is deleted or renamed here, the corresponding published file will also be deleted or renamed during the next deployment.

## Deployment

Deployment is automatic whenever website files are committed to the `main` branch.

To check the deployment status, open the **Actions** tab in this repository.

Public website:

https://ai4drr.github.io/chatbot/
