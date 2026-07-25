/**
 * ProTrack365 Dashboard - frontend server
 * -----------------------------------------
 * A plain Node/Express static server for the HTML/CSS/JS dashboard.
 * All actual data comes from the Flask-RESTful backend (see ../backend),
 * which this page calls directly via fetch() using the API_BASE constant
 * in public/js/app.js.
 */

const express = require("express");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.static(path.join(__dirname, "public")));

// Any non-file route falls back to the single-page dashboard.
app.get("*", (req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.listen(PORT, () => {
  console.log(`ProTrack365 frontend running at http://localhost:${PORT}`);
  console.log(`Expecting the backend API at the URL set in public/js/app.js (API_BASE)`);
});