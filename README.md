# ccauto

Pembungkus kecil untuk [Claude Code](https://code.claude.com): saat satu akun kena
limit, sesi dihentikan dan **dilanjutkan otomatis** (`--continue`) di akun berikutnya,
tanpa kamu mengetik apa pun. Kalau semua akun limit, ia berhenti dan menampilkan jam pulih.

*English: a tiny wrapper that, when one Claude Code account hits its usage limit,
relaunches the same session under the next of your own logged-in profiles.*

## ⚠️ Baca dulu

- **Hanya untuk akun milikmu sendiri**, dipakai olehmu sendiri. Jangan berbagi akun/kredensial
  (dilarang oleh [Consumer Terms](https://www.anthropic.com/legal/consumer-terms)).
- Berpindah akun otomatis untuk menghindari limit adalah **wilayah abu-abu**. Terms tidak
  menyebut limit secara eksplisit, tetapi melarang "bypassing any of our systems or protective
  measures", dan Anthropic boleh menangguhkan akun tanpa pemberitahuan dan tanpa refund.
  **Risiko sepenuhnya ada pada pemakai.** Proyek ini tidak berafiliasi dengan Anthropic.
- Cara paling aman: jadikan profil **API key** (bayar per pemakaian) sebagai cadangan terakhir.
- Deteksi limit berbasis **teks di layar**. Kalimat serupa di output (mis. log yang memuat
  "You've hit your limit") bisa memicu pindah akun yang salah. Tekan `Ctrl-C` lalu jalankan ulang.

## Cara kerja

1. Tiap akun punya satu direktori konfigurasi sendiri (`CLAUDE_CONFIG_DIR`), sehingga bisa
   login bersamaan: `~/.claude-profiles/<nama>`.
2. `ccauto` menjalankan `claude` di dalam pseudo-terminal, meneruskan input/output apa adanya,
   dan membaca layar untuk pesan limit.
3. Pesan limit terdeteksi + layar tenang 3 detik -> sesi dihentikan, akun ditandai limit
   sampai jam reset yang terbaca (cadangan 5 jam).
4. Profil berikutnya dibuka dengan `--continue` dan prompt "lanjutkan".
5. Semua akun limit -> berhenti (exit code 75) dan mencetak jam pulih tiap akun.

Akun dengan email kembar dan profil yang belum login dilewati otomatis.

## Prasyarat

- macOS atau Linux, Python 3.9+, `claude` (Claude Code) ada di `PATH`.
- Minimal dua profil yang sudah login. Untuk tiap akun:
  ```bash
  mkdir -p ~/.claude-profiles/akun1
  CLAUDE_CONFIG_DIR=~/.claude-profiles/akun1 claude auth login
  ```
  Ulangi untuk `akun2`, dst. Akun bawaan (`~/.claude`) dikenali sebagai profil `default`.
- Agar `--continue` menemukan sesi yang sama di semua profil, sesi harus terlihat bersama
  (mis. symlink `projects/` tiap profil ke satu folder). Tanpa itu, akun berikutnya memulai
  sesi baru di folder yang sama.

## Pasang

```bash
git clone https://github.com/saifulveri/ccauto.git ~/tools/ccauto
echo 'ccauto() { python3 "$HOME/tools/ccauto/ccauto.py" "$@"; }' >> ~/.zshrc
source ~/.zshrc
```

## Pakai

```bash
ccauto                 # mulai dari profil aktif ($CLAUDE_CONFIG_DIR, atau default)
ccauto akun2           # mulai dari profil akun2
ccauto --model opus    # flag claude biasa diteruskan
```

Pengaturan lewat environment variable:

| Variabel | Fungsi | Bawaan |
|---|---|---|
| `CCAUTO_PROFILES` | Urutan rotasi, dipisah koma, mis. `akun1,akun2,default` | semua profil login (email unik) + `default` |
| `CCAUTO_HOME` | Folder induk profil | `~/.claude-profiles` |

Status limit disimpan di `$CCAUTO_HOME/.ccauto-state.json`. Hapus file itu untuk mereset.

## Batasan

- Pindah = **restart** sesi. Langkah yang terputus di tengah bisa terulang sebagian.
- Hanya sesi yang dibuka lewat `ccauto` yang dipantau.
- Pola teks limit bisa berubah di versi Claude Code mendatang; sesuaikan `LIMIT_RE` di `ccauto.py`.
- Flag bernilai selain `--model`, `--permission-mode`, `--add-dir`, `--effort`, `--settings`
  tidak diteruskan ke akun pengganti.

## Keamanan

Skrip tidak membaca, menyalin, atau mengirim kredensial. Ia hanya menjalankan `claude auth status`
(untuk mengambil email) dan `claude` dengan `CLAUDE_CONFIG_DIR` berbeda. **Jangan pernah commit
folder profil** (`~/.claude-profiles`): isinya token login.

## Lisensi

MIT, lihat [LICENSE](LICENSE).
