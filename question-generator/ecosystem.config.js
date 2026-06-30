// Twice-daily batch job: cron_restart fires run.sh, which exits when done.
// run.sh merges (--cache + --append) so the two runs accumulate into today's file.
//   pm2 start ecosystem.config.js && pm2 save
module.exports = {
  apps: [
    {
      name: "question-generator",
      script: "./run.sh",
      interpreter: "bash",
      cwd: __dirname,
      autorestart: false,
      // 11:00 (mid-day freshness) + 23:00 (full day's news) server time — adjust as needed.
      cron_restart: "0 11,23 * * *",
      time: true,
    },
  ],
};
