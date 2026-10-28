# KVKK hazırlık değerlendirmesi (teknik taslak)

> **Bu belge hukuki görüş değildir ve hukuki onay verilmemiştir.** Veri sorumlusu (işveren), KVKK uyum sorumlusu /
> hukuk birimi ve İSG uzmanı tarafından incelenip onaylanmadan sistem gerçek çalışanların bulunduğu sahada
> kullanılmamalıdır. Durum: **NO-GO (hukuki inceleme bekleniyor)**.
> Referans: KVKK, "İş yerlerinde güvenlik kamerası sistemi kullanımında dikkat edilecek hususlar" kamuoyu duyurusu —
> https://www.kvkk.gov.tr/Icerik/8770/

## Amaç ve kapsam sınırı

Amaç: şantiyede baret/yelek eksikliğini **iş sağlığı ve güvenliği** amacıyla yetkili kişiye bildirmek ve insan
incelemesine sunmak. Kapsam dışı (sistemde YOK ve eklenmemeli): yüz tanıma, kişi kimliklendirme, personel puanlama,
otomatik disiplin/yaptırım, performans takibi, ses kaydı, işyeri dışı izleme.

## Kişisel veri envanteri

| Veri | Kişisel veri mi | Nerede | Saklama (bugün) | Erişim |
|---|---|---|---|---|
| Canlı video kareleri | Evet (görüntü) | RAM, ağ (WS/MJPEG) | yalnız son kare, kalıcı değil | supervisor+, kapsamlı |
| İhlal snapshot'ları | Evet (görüntü) | `data/snapshots`, yedekler | **süresiz** (silme yok) | supervisor+, kapsamlı |
| İz ID (ByteTrack) | Dolaylı (run içinde takma ad) | DB | süresiz | viewer+ |
| Eksik ekipman, zaman, kamera | Dolaylı (görüntüyle birleşince) | DB | süresiz | viewer+ |
| Kişi (iz) raporu | Dolaylı | DB | süresiz | viewer+ |
| Kullanıcı hesabı | Evet (çalışan/yetkili) | `app_users` | hesap süresince | admin |
| Denetim kaydı (kullanıcı, IP, olay) | Evet | `audit_log` | süresiz | admin |

Biyometrik işleme yapılmaz (yüz şablonu çıkarılmaz). Görüntüler yine de kişisel veridir.

## Değerlendirilmesi gerekenler (veri sorumlusu)

1. **Hukuki dayanak** (KVKK md. 5): olası dayanaklar (ör. 6331 sayılı İSG Kanunu kapsamındaki yükümlülükler, meşru
   menfaat) hukuk birimince belirlenmeli; açık rıza işveren-çalışan ilişkisinde tek dayanak olarak uygun olmayabilir.
2. **Aydınlatma** (md. 10): kamera konumları, amaç, saklama süresi, erişen roller, haklar ve başvuru kanalı; sahada
   görünür levha ve yazılı bildirim. Taslak metin hazırlanmadı.
3. **Ölçülülük**: kamera açıları yalnız çalışma alanı; dinlenme/soyunma/WC alanları kesinlikle kapsam dışı.
4. **Saklama ve imha**: bugün otomatik silme yok. Önerilen: snapshot'lar N gün (ör. 30), olay kayıtları M ay; imha
   kaydı tutulmalı (KVKK Kişisel Verilerin Silinmesi Yönetmeliği). Teknik araç FAZ 7+ işidir; uygulanmadı.
5. **VERBİS** kaydı ve kişisel veri işleme envanterine eklenmesi.
6. **İlgili kişi hakları** (md. 11): başvuru alma, görüntü erişim talebi süreci (başka kişilerin maskelenmesi gerekir —
   araç yok).
7. **Yurt dışı aktarım**: sistem yerel çalışır; bulut yedek kullanılırsa ayrıca değerlendirilmeli.
8. **Veri güvenliği** (md. 12): aşağıdaki teknik tedbirler + idari tedbirler (yetki matrisi onayı, eğitim, gizlilik
   taahhüdü).

## Teknik tedbirler (uygulandı, FAZ 6)

Rol bazlı erişim (viewer görüntü/canlı yayın göremez), kamera kapsamı, oturum süresi, parola özeti, denetim kaydı,
localhost varsayılanı, TLS tasarımı, en az yetkili DB rolü, gizli bilgi maskeleme, çıkışta tarayıcıdaki verinin
temizlenmesi.

**Eksik:** yedek şifrelemesi, otomatik saklama/imha, görüntüde üçüncü kişi maskeleme, denetim kaydı saklama süresi,
denetim kaydının kurcalanmaya dayanıklı (WORM) saklanması.

## Rol matrisi (önerilen sahiplik)

| Rol | Kim | Görür |
|---|---|---|
| viewer | yönetim, raporlama | istatistik, ihlal listesi (görüntüsüz) |
| supervisor | İSG uzmanı / saha güvenlik sorumlusu | + canlı yayın, ihlal görüntüleri (yalnız atanan kameralar) |
| admin | BT/sistem yöneticisi (az sayıda) | + kullanıcı yönetimi, denetim kaydı |

## İnsan inceleme akışı (önerilen)

Uyarı → supervisor görüntüyü inceler → gerçek ihlalse **yerinde, sözlü İSG müdahalesi** (eğitim/uyarı) → yanlış alarmsa
not edilir. Sistem çıktısı tek başına disiplin işlemine dayanak yapılmaz; model hatalı olabilir (FAZ 8 ölçümü
yapılmadı). Kamera/sistem arızasında mevcut İSG prosedürü aynen geçerlidir.
