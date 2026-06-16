// Daily batch job: cron_restart fires run.sh once a day, then it exits.
//   pm2 start ecosystem.config.js && pm2 save
module.exports = {
  apps: [
    {
      name: "question-generator",
      script: "./run.sh",
      interpreter: "bash",
      cwd: __dirname,
      autorestart: false,
      cron_restart: "0 6 * * *", // every day at 06:00 server time — adjust as needed
      time: true,
    },
  ],
};
