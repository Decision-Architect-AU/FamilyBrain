FROM mcr.microsoft.com/playwright:v1.45.0-jammy
WORKDIR /app
COPY package.json .
RUN npm install
# Install Playwright browsers (chromium only to keep image lean)
RUN npx playwright install chromium
COPY src ./src
CMD ["node", "src/index.js"]
