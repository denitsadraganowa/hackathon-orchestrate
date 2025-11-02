// server.js
import express from "express";
import fetch from "node-fetch";

const app = express();

app.get("/api/getToken", async (_req, res) => {
  try {
    const iamRes = await fetch("https://iam.cloud.ibm.com/identity/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
      },
      body: new URLSearchParams({
        grant_type: "urn:ibm:params:oauth:grant-type:apikey",
        apikey: process.env.IBM_CLOUD_API_KEY, // store securely
      }),
    });

    if (!iamRes.ok) {
      const txt = await iamRes.text();
      return res.status(500).json({ error: `IAM error ${iamRes.status}: ${txt}` });
    }

    const token = await iamRes.json(); // { access_token, expires_in, ... }
    res.json({ access_token: token.access_token });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.listen(3000, () => console.log("Server running on :3000"));
